from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import BinaryIO, Literal
from uuid import UUID

from content.run_artifacts import artifact_path
from utils.doc_utils.os_utils import is_link


ORIGINALS_NAMESPACE = 'session-originals-v1'


def valid_file_name(name: str) -> bool:
    return (
        bool(name) and len(name.encode('utf-8')) <= 200 and name == name.strip()
        and not name.endswith('.') and all(char.isprintable() and char not in '/\\:<>"|?*' for char in name)
        and not PureWindowsPath(name).is_reserved()
    )


def upload_key(file_id: UUID, name: str) -> str:
    from core.config import USER_UPLOAD_DIR

    return f'/{USER_UPLOAD_DIR}/{file_id}/{name}'


@dataclass(frozen=True)
class WorkspaceFile:
    id: UUID
    name: str
    kind: Literal['source', 'artifact']
    run_id: UUID | None = None
    revision: str | None = None
    workspace_key: str | None = None

    @property
    def key(self) -> str:
        return self.workspace_key or upload_key(self.id, self.name)

    def path(self, session_id: UUID) -> Path:
        if self.run_id is not None:
            return artifact_path(self.run_id, self.id)
        from core import config

        originals = Path(config.ROOT_PATH_AGENT).resolve() / ORIGINALS_NAMESPACE
        session = originals / str(session_id)
        path = session / str(self.id) / self.name
        if any(is_link(part, missing=False) for part in (originals, session, path.parent, path)):
            raise ValueError('原始文件必须位于常规目录中。')
        return path

    def workspace_path(self, session_id: UUID) -> Path:
        from content.workspace import get_workspace_root

        root = get_workspace_root({'configurable': {'workspace_id': str(session_id)}})
        path = root
        for part in self.key.lstrip('/').split('/'):
            if not valid_file_name(part):
                raise ValueError('工作区文件路径无效。')
            path /= part
            if is_link(path, missing=False):
                raise ValueError('工作区文件必须位于常规目录中。')
        return path

    def save(self, session_id: UUID, source: BinaryIO) -> tuple[Path, Path]:
        original = self.path(session_id)
        working_copy = self.workspace_path(session_id)
        with ExitStack() as rollback:
            for path in (original, working_copy):
                path.parent.mkdir(parents=True, exist_ok=False)
                rollback.callback(self.discard, path)
            source.seek(0)
            with original.open('xb') as saved, working_copy.open('xb') as available:
                while chunk := source.read(1024 * 1024):
                    saved.write(chunk)
                    available.write(chunk)
            rollback.pop_all()
        return original, working_copy

    @staticmethod
    def discard(path: Path) -> None:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
