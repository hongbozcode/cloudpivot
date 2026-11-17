import asyncio
import os
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage

from core import config
from utils.doc_utils import base64_utils, os_utils


class FileState(AgentState):
    before_paths: set[str]
    directory_context: str
    upload_context: str | None
    upload_files: list | None


class FileMiddle(AgentMiddleware):
    state_schema = FileState
    EXCLUDED_DIRS = {'conversation_history'}
    PATH_ARGUMENTS = {'reade_file_path', 'write_file_path', 'image_path', 'reference_image_path'}
    LEGACY_CONTEXT_PREFIXES = ('当前目录结构:\n', '用户上传了文件, 路径如下: ')

    def _save_uploads(self, upload_files):
        root = Path(config.ROOT_PATH_AGENT).resolve()
        upload_dir = root / config.USER_UPLOAD_DIR
        if os_utils.resolve_within(upload_dir, root) != upload_dir:
            raise ValueError('上传目录必须使用工作目录下的常规目录。')
        uploads = []
        for file in upload_files:
            filename = file.get('metadata', {}).get('filename')
            if (
                not isinstance(filename, str)
                or not filename
                or filename.endswith(('.', ' '))
                or any(char in filename for char in '/\\:<>"|?*\x00')
                or PureWindowsPath(filename).is_reserved()
            ):
                raise ValueError('上传文件必须使用有效的文件名。')
            os_utils.resolve_within(upload_dir / filename, upload_dir)
            uploads.append((filename, file.get('data')))

        upload_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for filename, data in uploads:
            path = upload_dir / filename
            try:
                base64_utils.base64_to_file(data, path)
            except FileExistsError:
                path = upload_dir / f'{uuid4().hex}{path.suffix}'
                base64_utils.base64_to_file(data, path)
            saved.append('/' + path.relative_to(root).as_posix())
        return saved

    async def abefore_agent(self, state, runtime):
        await asyncio.to_thread(Path(config.ROOT_PATH_AGENT).mkdir, parents=True, exist_ok=True)
        updates = {'upload_context': None}

        # Remove synthetic messages previously persisted by this middleware.
        legacy_messages = [
            RemoveMessage(id=message.id)
            for message in state.get('messages', [])
            if isinstance(message, ToolMessage)
            and message.name is None
            and message.id
            and isinstance(message.content, str)
            and message.content.startswith(self.LEGACY_CONTEXT_PREFIXES)
        ]
        if legacy_messages:
            updates['messages'] = legacy_messages

        if upload_files := state.get('upload_files'):
            saved = await asyncio.to_thread(self._save_uploads, upload_files)
            updates['upload_context'] = '用户上传的文件路径：\n' + '\n'.join(saved)
            updates['upload_files'] = None
        return updates

    def before_model(self, state, runtime):
        root = config.ROOT_PATH_AGENT
        current_paths = os_utils.get_all_files(root, exclude=self.EXCLUDED_DIRS)
        if state.get('directory_context') is None or current_paths != state.get('before_paths'):
            directory = os_utils.list_directory(root, exclude=self.EXCLUDED_DIRS)
            return {'directory_context': '当前目录结构：\n' + (directory or '（空）'),
                    'before_paths': current_paths}
        return None

    async def awrap_model_call(self, request, handler):
        context = '\n\n'.join(filter(None, (
            request.state.get('directory_context'),
            request.state.get('upload_context'),
        )))
        if context:
            note = HumanMessage(content='下面是应用提供的工作区信息：\n' + context)
            request = request.override(messages=[note, *request.messages])
        return await handler(request)

    def change_file_path(self, path):
        configured_root = Path(config.ROOT_PATH_AGENT)
        root = configured_root.resolve()
        path = Path(path)
        if path.is_relative_to(root) or path.is_relative_to(configured_root):
            candidate = path
        elif path.drive:
            raise ValueError('文件路径必须位于工作目录内。')
        else:
            virtual_path = path.relative_to(path.anchor) if path.anchor else path
            candidate = root / virtual_path
        return str(os_utils.resolve_within(candidate, root))

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        args = call['args']
        try:
            resolved_args = {
                name: self.change_file_path(value) if name in self.PATH_ARGUMENTS else value
                for name, value in args.items()
            }
        except (ValueError, OSError, RuntimeError):
            return ToolMessage(
                content='文件路径必须位于工作目录内，并且使用有效名称。',
                name=call['name'],
                tool_call_id=call['id'],
                status='error',
            )

        if resolved_args != args:
            request = request.override(tool_call={**call, 'args': resolved_args})
        response = await handler(request)
        if isinstance(response, ToolMessage) and isinstance(response.content, str) and response.content:
            # Keep the paths shown to the model relative to the virtual workspace.
            content = os.path.normpath(response.content)
            root = str(Path(config.ROOT_PATH_AGENT).resolve())
            response.content = content.replace(root, '').replace(config.ROOT_PATH_AGENT, '')
        return response
