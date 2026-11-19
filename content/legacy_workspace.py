import json
import shutil
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from content.run_artifacts import ARTIFACT_NAMESPACE
from content.session_files import ORIGINALS_NAMESPACE, WorkspaceFile, valid_file_name
from content.workspace import WORKSPACE_NAMESPACE, get_workspace_root
from utils.doc_utils.os_utils import is_link, iter_files


class InvalidWorkspace(ValueError):
    pass


class WorkspaceChanged(ValueError):
    pass


@dataclass(frozen=True)
class LegacyWorkspace:
    root: Path
    entries: dict[Path, tuple[int, int, int]]
    files: list[str]
    skills: list[str]
    skill_files: list[str]

    def confirmation(self, account_id: UUID) -> str:
        payload = [
            str(self.root), str(account_id),
            [(path.as_posix(), info) for path, info in self.entries.items()],
        ]
        return sha256(json.dumps(payload).encode()).hexdigest()


def legacy_source(base: Path, source_type: str, source: str) -> Path:
    if source_type == 'session':
        if not valid_file_name(source):
            raise InvalidWorkspace('旧会话 ID 无效。')
        hashed = base / WORKSPACE_NAMESPACE / sha256(source.encode()).hexdigest()
        if not is_link(hashed.parent, missing=False) and not is_link(hashed) and hashed.is_dir():
            return hashed
        return base / source
    path = Path(source)
    if path.is_absolute():
        return path
    if path.anchor:
        raise InvalidWorkspace('请输入旧工作区根目录内的路径。')
    return base / path


def regular_directory(path: Path, base: Path) -> None:
    if not path.is_relative_to(base):
        raise InvalidWorkspace('旧工作区目录必须位于配置的旧工作区根目录内。')
    current = base
    for part in path.relative_to(base).parts:
        if not valid_file_name(part):
            raise InvalidWorkspace('旧工作区目录无效。')
        current /= part
        if is_link(current):
            raise InvalidWorkspace('找不到旧工作区目录，或目录包含链接。')
    if not current.is_dir():
        raise InvalidWorkspace('找不到旧工作区目录。')


def workspace_manifest(root: Path) -> LegacyWorkspace:
    from core import agent_setting as config

    entries, files, skills, skill_files = {}, [], [], []
    excluded = (WORKSPACE_NAMESPACE, ORIGINALS_NAMESPACE, ARTIFACT_NAMESPACE, 'builtin-skills')
    for path in sorted(iter_files(root, ignore=(*excluded, config.CONVERSATION_HISTORY_PATH, '.git', '.env'))):
        relative = path.relative_to(root)
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            continue
        if any(not valid_file_name(part) for part in relative.parts):
            raise InvalidWorkspace('旧工作区包含无法迁入的文件名。')
        entries[relative] = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if relative.parts[0] != config.SKILL_DIR_PATH:
            files.append(relative.as_posix())
        elif path.name == 'SKILL.md':
            skills.append(relative.as_posix())
        else:
            skill_files.append(relative.as_posix())
    if not entries:
        raise InvalidWorkspace('旧工作区没有可迁入的文件或会话 Skill。')
    return LegacyWorkspace(root, entries, files, skills, skill_files)


def copy_workspace(source: LegacyWorkspace, session_id: UUID, created: list[Path]) -> list[WorkspaceFile]:
    root = get_workspace_root({'configurable': {'workspace_id': str(session_id)}})
    root.mkdir(parents=True, exist_ok=False)
    created.append(root)
    files = []
    visible_files = set(source.files)
    for relative in source.entries:
        origin = source.root / relative
        regular_directory(origin.parent, source.root)
        if is_link(origin):
            raise WorkspaceChanged
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        key = relative.as_posix()
        if key in visible_files:
            file = WorkspaceFile(uuid4(), relative.name, 'source', workspace_key=f'/{key}')
            original = file.path(session_id)
            if not files:
                original.parent.parent.mkdir(parents=True, exist_ok=False)
                created.append(original.parent.parent)
            original.parent.mkdir()
            shutil.copyfile(target, original)
            files.append(file)
    if workspace_manifest(source.root).entries != source.entries:
        raise WorkspaceChanged
    return files
