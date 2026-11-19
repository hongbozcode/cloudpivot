import asyncio
from pathlib import PureWindowsPath
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage

from connection.llm import WORKSPACE_CONTEXT
from content.workspace import get_workspace_root
from core import config
from utils.doc_utils import base64_utils, os_utils


class FileState(AgentState):
    directory_context: str
    upload_context: str | None
    upload_files: list | None


class FileMiddle(AgentMiddleware):
    state_schema = FileState
    EXCLUDED_DIRS = {config.CONVERSATION_HISTORY_PATH}
    LEGACY_CONTEXT_PREFIXES = ('当前目录结构:\n', '用户上传了文件, 路径如下: ')

    def _save_uploads(self, upload_files):
        root = get_workspace_root()
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
        root = get_workspace_root()
        directory = os_utils.list_directory(root, exclude=self.EXCLUDED_DIRS) if root.is_dir() else ''
        directory_context = '当前目录结构：\n' + (directory or '（空）')
        if state.get('directory_context') != directory_context:
            return {'directory_context': directory_context}
        return None

    async def abefore_model(self, state, runtime):
        return await asyncio.to_thread(self.before_model, state, runtime)

    async def awrap_model_call(self, request, handler):
        context = '\n\n'.join(filter(None, (
            request.state.get('directory_context'),
            request.state.get('upload_context'),
        )))
        if context:
            note = HumanMessage(
                content='下面是应用提供的工作区信息。虚拟根目录为 /。\n' + context,
                additional_kwargs={WORKSPACE_CONTEXT: True},
            )
            request = request.override(messages=[note, *request.messages])
        return await handler(request)
