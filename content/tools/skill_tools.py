from shutil import copyfileobj

from langchain.tools import tool

from content.middleware.run_actions import current_actions
from content.skills_package import PERSONAL_SKILLS_PATH
from content.workspace import get_workspace_root
from utils.doc_utils.os_utils import resolve_within, resolve_workspace_path, workspace_path


@tool
def copy_skill_asset(source_path: str, destination_path: str) -> str:
    """将 /personal-skills/ 下的个人 Skill 配套文件复制到会话工作区的新路径，以便处理模板或其他资料。"""
    actions = current_actions.get()
    if actions is None or actions.personal_skills is None or not source_path.startswith(PERSONAL_SKILLS_PATH):
        return '请选择当前运行启用的个人 Skill 配套文件。'
    try:
        root = get_workspace_root()
        source = resolve_within(actions.personal_skills / source_path.removeprefix(PERSONAL_SKILLS_PATH), actions.personal_skills)
        destination = resolve_workspace_path(destination_path, root)
        namespace, *_ = destination.relative_to(root).parts
        if namespace.casefold() in ('personal-skills', 'builtin-skills'):
            raise ValueError('目标路径必须位于可写的会话工作区。')
        with source.open('rb') as original:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as copy:
                try:
                    copyfileobj(original, copy)
                except OSError:
                    copy.close()
                    destination.unlink(missing_ok=True)
                    raise
        return workspace_path(destination, root)
    except FileExistsError:
        return '目标文件已存在，请选择新的工作区路径。'
    except (OSError, ValueError):
        return '复制失败，请检查个人 Skill 配套文件和工作区路径。'
