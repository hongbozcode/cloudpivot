import re
import stat
from codecs import BOM_UTF8
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PureWindowsPath
from uuid import UUID
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo
from zlib import error as CompressionError

from yaml import YAMLError, safe_load

from content.workspace import get_workspace_root
from core.agent_setting import SKILL_DIR_PATH
from utils.doc_utils.os_utils import is_link, iter_files


PERSONAL_SKILLS_PATH = '/personal-skills/'
SKILL_FILENAME = 'SKILL.md'
MAX_PACKAGE_BYTES = 25 * 1024 * 1024
MAX_MARKDOWN_BYTES = 256 * 1024
MAX_FILES = 128
_FRONTMATTER = re.compile(r'\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?\n', re.MULTILINE | re.DOTALL)
_SKILL_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


class SkillFormatError(ValueError):
    pass


def valid_skill_name(name: str) -> bool:
    return (
        len(name) <= 64 and name != 'skill-creator' and bool(_SKILL_NAME.fullmatch(name))
        and not PureWindowsPath(name).is_reserved()
    )


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    markdown: str

    @classmethod
    def parse(cls, data: bytes, directory: str | None = None):
        if len(data) > MAX_MARKDOWN_BYTES:
            raise SkillFormatError('SKILL.md 不能超过 256 KB。')
        try:
            markdown = data.decode('utf-8')
            match = _FRONTMATTER.match(markdown)
            fields = safe_load(match[1]) if match else None
        except (YAMLError, TypeError, ValueError, RecursionError) as exc:
            raise SkillFormatError('请使用 UTF-8 编写 SKILL.md，并检查开头的 YAML 格式。') from exc
        if not isinstance(fields, dict) or '\x00' in markdown:
            raise SkillFormatError('SKILL.md 必须以 --- 包裹的 YAML 元数据开头。')
        name, description = fields.get('name'), fields.get('description')
        if not isinstance(name, str) or not valid_skill_name(name):
            raise SkillFormatError('Skill 的 name 必须使用小写字母、数字和单个连字符，且不能使用保留名称。')
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
            raise SkillFormatError('请填写不超过 1024 字符的 Skill description。')
        if directory is not None and name != directory:
            raise SkillFormatError('SKILL.md 的 name 必须与会话 Skill 目录名一致。')
        return cls(name, description.strip(), markdown)


def _validate_paths(files: list[str]) -> None:
    if len(set(files)) != len(files):
        raise SkillFormatError('Skill 包含重复文件。')
    seen: dict[str, tuple[str, bool]] = {}
    for path in files:
        parts = path.split('/')
        if len(path) > 512 or any(
            not part or not part.isprintable() or len(part.encode('utf-8')) > 255 or part.endswith(('.', ' '))
            or any(char in part for char in '\\:<>"|?*~') or PureWindowsPath(part).is_reserved()
            for part in parts
        ):
            raise SkillFormatError('Skill 包含无效文件路径。')
        for length in range(1, len(parts) + 1):
            entry = '/'.join(parts[:length])
            is_dir = length < len(parts)
            previous = seen.setdefault(entry.casefold(), (entry, is_dir))
            if previous != (entry, is_dir):
                raise SkillFormatError('Skill 包含重复文件或目录名称冲突。')


@dataclass(frozen=True)
class SkillBundle:
    definition: SkillDefinition
    files: dict[str, bytes]

    def revision(self) -> str:
        digest = sha256()
        for path, content in sorted(self.files.items()):
            digest.update(path.encode('utf-8') + b'\x00')
            digest.update(sha256(content).digest())
        return digest.hexdigest()

    def pack(self) -> bytes:
        out = BytesIO()
        with ZipFile(out, 'w') as archive:
            for path, content in sorted(self.files.items()):
                archive.writestr(ZipInfo(path), content, compress_type=ZIP_DEFLATED)
        return out.getvalue()

    @classmethod
    def upload(cls, filename: str, content: bytes):
        if filename.casefold() == SKILL_FILENAME.casefold():
            content = content.removeprefix(BOM_UTF8)
            definition = SkillDefinition.parse(content)
            return cls(definition, {SKILL_FILENAME: content})
        if not filename.lower().endswith('.zip'):
            raise SkillFormatError('请选择 SKILL.md 或 ZIP 文件。')
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                files = [entry for entry in entries if not entry.is_dir()]
                if len(entries) > MAX_FILES * 2 or len(files) > MAX_FILES:
                    raise SkillFormatError(f'Skill 包最多包含 {MAX_FILES} 个文件。')
                if sum(entry.file_size for entry in files) > MAX_PACKAGE_BYTES:
                    raise SkillFormatError('Skill 包解压后不能超过 25 MB。')
                if any(entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED) for entry in files):
                    raise SkillFormatError('请使用普通 ZIP 压缩（存储或 Deflate）。')
                if any(stat.S_IFMT(entry.external_attr >> 16) not in (
                    0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG,
                ) for entry in entries):
                    raise SkillFormatError('Skill 包只能包含普通文件和目录。')
                _validate_paths([entry.orig_filename for entry in files])
                _validate_paths([entry.orig_filename.removesuffix('/') for entry in entries if entry.is_dir()])
                # Accept an archive rooted at the Skill or at one enclosing folder.
                manifests = [entry for entry in files if entry.filename.rsplit('/', 1)[-1] == SKILL_FILENAME]
                if len(manifests) != 1:
                    raise SkillFormatError('ZIP 中必须包含且仅包含一个 SKILL.md。')
                manifest = manifests[0]
                if manifest.file_size > MAX_MARKDOWN_BYTES:
                    raise SkillFormatError('SKILL.md 不能超过 256 KB。')
                prefix = manifest.filename.removesuffix(SKILL_FILENAME)
                if prefix.count('/') > 1 or any(not entry.filename.startswith(prefix) for entry in files):
                    raise SkillFormatError('请将 SKILL.md 和配套文件放在 ZIP 根目录或同一个顶层目录下。')
                markdown = _read_zip_file(archive, manifest).removeprefix(BOM_UTF8)
                definition = SkillDefinition.parse(markdown)
                return cls(definition, {
                    SKILL_FILENAME: markdown,
                    **{entry.filename.removeprefix(prefix): _read_zip_file(archive, entry) for entry in files if entry != manifest},
                })
        except SkillFormatError:
            raise
        except (BadZipFile, CompressionError, EOFError, OSError, RuntimeError, ValueError) as exc:
            raise SkillFormatError('无法读取 ZIP，请检查压缩包是否损坏或受密码保护。') from exc


def _read_zip_file(archive: ZipFile, entry: ZipInfo) -> bytes:
    with archive.open(entry) as source:
        content = source.read(entry.file_size + 1)
    if len(content) != entry.file_size:
        raise SkillFormatError('ZIP 中的文件大小与压缩包记录不一致。')
    return content


def unpack_bundle(data: bytes) -> dict[str, bytes]:
    # The database contains only packages already validated and rewritten by pack().
    with ZipFile(BytesIO(data)) as archive:
        return {path: archive.read(path) for path in archive.namelist()}


def materialize_skills(root: Path, packages: tuple[tuple[str, bytes], ...]) -> None:
    for name, data in packages:
        for relative, content in unpack_bundle(data).items():
            path = root / name / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def _session_skills_root(session_id: UUID) -> Path:
    return get_workspace_root({'configurable': {'workspace_id': str(session_id)}}) / SKILL_DIR_PATH


def _read_file(path: Path, maximum: int) -> bytes:
    with path.open('rb') as file:
        content = file.read(maximum + 1)
    if len(content) > maximum:
        raise SkillFormatError('SKILL.md 或配套文件超出大小上限。')
    return content


def _session_definition(directory: Path) -> SkillDefinition:
    manifest = directory / SKILL_FILENAME
    if is_link(manifest) or not manifest.is_file():
        raise FileNotFoundError(SKILL_FILENAME)
    return SkillDefinition.parse(_read_file(manifest, MAX_MARKDOWN_BYTES), directory.name)


def list_session_skills(session_id: UUID) -> list[SkillDefinition]:
    root = _session_skills_root(session_id)
    if is_link(root) or not root.is_dir():
        return []
    skills = []
    for directory in sorted(root.iterdir()):
        if not valid_skill_name(directory.name) or is_link(directory) or not directory.is_dir():
            continue
        try:
            skills.append(_session_definition(directory))
        except (SkillFormatError, OSError):
            continue
    return skills


def read_session_skill(session_id: UUID, name: str) -> SkillBundle:
    root = _session_skills_root(session_id)
    directory = root / name
    if not valid_skill_name(name) or is_link(root) or is_link(directory) or not directory.is_dir():
        raise FileNotFoundError(SKILL_FILENAME)
    definition = _session_definition(directory)
    files = {SKILL_FILENAME: definition.markdown.encode('utf-8')}
    remaining = MAX_PACKAGE_BYTES - len(files[SKILL_FILENAME])
    for path in iter_files(directory, exclude=(SKILL_FILENAME,)):
        if not stat.S_ISREG(path.stat().st_mode):
            continue
        if len(files) >= MAX_FILES:
            raise SkillFormatError(f'Skill 包最多包含 {MAX_FILES} 个文件。')
        content = _read_file(path, remaining)
        files[path.relative_to(directory).as_posix()] = content
        remaining -= len(content)
    _validate_paths(list(files))
    return SkillBundle(definition, files)
