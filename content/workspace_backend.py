import asyncio
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    WriteResult,
)

from content.workspace import get_workspace_root
from core.agent_setting import SKILL_DIR_PATH


class SessionFilesystemBackend(BackendProtocol):
    """Resolve the LangGraph thread each time the shared graph accesses files.

    The protocol's async methods run these operations in a worker thread.
    FilesystemBackend creates parent directories on writes.
    """

    @staticmethod
    def _backend() -> FilesystemBackend:
        return FilesystemBackend(root_dir=get_workspace_root(), virtual_mode=True)

    def ls(self, path: str):
        backend = self._backend()
        if path.rstrip('/') in ('', f'/{SKILL_DIR_PATH}') and not (backend.cwd / path.lstrip('/')).exists():
            return LsResult(entries=[])
        return backend.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000):
        return self._backend().read(file_path, offset, limit)

    def write(self, file_path: str, content: str):
        return self._backend().write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        return self._backend().edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str):
        return self._backend().delete(file_path)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *,
             max_count: int | None = None, context_lines: int = 0):
        backend = self._backend()
        if path in (None, '/') and not backend.cwd.exists():
            return GrepResult(matches=[])
        return backend.grep(pattern, path, glob, max_count=max_count, context_lines=context_lines)

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None, *,
                    max_count: int | None = None, context_lines: int = 0):
        if context_lines:
            return await asyncio.to_thread(
                self.grep, pattern, path, glob, max_count=max_count, context_lines=context_lines
            )
        return await super().agrep(pattern, path, glob, max_count=max_count)

    def glob(self, pattern: str, path: str | None = None):
        backend = self._backend()
        if path in (None, '/') and not backend.cwd.exists():
            return GlobResult(matches=[])
        return backend.glob(pattern, path)

    def upload_files(self, files: list[tuple[str, bytes]]):
        return self._backend().upload_files(files)

    def download_files(self, paths: list[str]):
        return self._backend().download_files(paths)


class BuiltinSkillsBackend(FilesystemBackend):
    """Serve the packaged skills without copying them into every workspace."""

    def write(self, file_path: str, content: str):
        return WriteResult(error='内置 Skill 只读。请在 /skills 下创建会话 Skill。')

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        return EditResult(error='内置 Skill 只读。请在 /skills 下创建会话 Skill。')

    def delete(self, file_path: str):
        return DeleteResult(error='内置 Skill 只读。')

    def upload_files(self, files: list[tuple[str, bytes]]):
        return [FileUploadResponse(path=path, error='permission_denied') for path, _ in files]


def create_workspace_backend() -> CompositeBackend:
    builtin = BuiltinSkillsBackend(root_dir=Path(__file__).parent / 'skills', virtual_mode=True)
    return CompositeBackend(default=SessionFilesystemBackend(), routes={'/builtin-skills/': builtin})
