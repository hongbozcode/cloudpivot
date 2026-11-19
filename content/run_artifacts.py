import asyncio
import logging
import shutil
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from utils.doc_utils.os_utils import is_link, iter_files, resolve_within

logger = logging.getLogger(__name__)
ARTIFACT_NAMESPACE = 'run-artifacts-v1'


def artifact_path(run_id: UUID, artifact_id: UUID) -> Path:
    from core import config

    base = Path(config.ROOT_PATH_AGENT).resolve()
    directory = base / ARTIFACT_NAMESPACE
    path = directory / str(run_id) / str(artifact_id)
    if any(is_link(part, missing=False) for part in (directory, path.parent, path)):
        raise ValueError('运行产物必须位于常规目录中。')
    return path


def _workspace_files(root: Path) -> dict[Path, tuple[int, int, int]]:
    from core import config

    files = {}
    for path in iter_files(root, exclude=(config.USER_UPLOAD_DIR, config.CONVERSATION_HISTORY_PATH, config.SKILL_DIR_PATH)):
        relative = path.relative_to(root)
        if any(part.startswith('.') for part in relative.parts):
            continue
        try:
            info = resolve_within(path, root).stat()
        except (ValueError, OSError):
            continue
        if stat.S_ISREG(info.st_mode):
            files[relative] = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return files


class RunArtifacts:
    def __init__(self, run_id: UUID, session_id: UUID, publish: Callable[[dict], Awaitable[None]]):
        from content.workspace import get_workspace_root

        self.run_id = run_id
        self.root = get_workspace_root({'configurable': {'thread_id': str(session_id)}})
        self.publish = publish
        self.files: dict[Path, tuple[int, int, int]] = {}
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        self.files = await asyncio.to_thread(_workspace_files, self.root)

    async def capture(self) -> None:
        async with self.lock:
            files = await asyncio.to_thread(_workspace_files, self.root)
            for relative, fingerprint in files.items():
                if self.files.get(relative) == fingerprint:
                    continue
                artifact_id = uuid5(self.run_id, relative.as_posix())
                try:
                    await asyncio.to_thread(self._copy, relative, artifact_id)
                except (OSError, ValueError) as exc:
                    logger.warning('Could not save artifact for run %s (%s)', self.run_id, type(exc).__name__)
                    continue
                name = ''.join(char for char in relative.name if char.isprintable()).strip()[:200] or '生成文件'
                await self.publish({'type': 'artifact', 'artifact_id': str(artifact_id), 'name': name})
                self.files[relative] = fingerprint

    def _copy(self, relative: Path, artifact_id: UUID) -> None:
        source = self.root / relative
        if is_link(source) or resolve_within(source, self.root) != source or not source.is_file():
            raise ValueError('无法保存工作区文件。')
        path = artifact_path(self.run_id, artifact_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(str(uuid4()))
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
