import datetime
import platform

from langchain.tools import tool

from content.workspace import get_workspace_root
from utils.doc_utils.os_utils import is_link, resolve_workspace_path


def parse_command(command):
    if not isinstance(command, str) or any(char in command for char in '\r\n\x00'):
        return None
    command = command.strip()
    name = command.lower()
    if name in ('pwd', 'uname -s', 'python --version'):
        return name, None

    parts = command.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != 'ls':
        return None
    path = parts[1]
    if path[0] in ('"', "'"):
        if len(path) < 3 or path[-1] != path[0]:
            return None
        path = path[1:-1]
    return 'ls', path


@tool
def get_current_time():
    """
    获取当前时间
    :return: 当前时间
    """
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@tool
def run_command(command: str):
    """
    根据用户明确指定的命令查询运行环境。
    支持 pwd、uname -s、python --version 和 ls <工作区路径>。带空格的路径可使用引号。
    :param command: 一条受支持的查询命令
    """
    parsed = parse_command(command)
    if parsed is None:
        return '请选择支持的查询：pwd、uname -s、python --version 或 ls <工作区路径>。'

    name, path = parsed
    match name:
        case 'pwd':
            return '工作区路径：/'
        case 'uname -s':
            return platform.system()
        case 'python --version':
            return f'Python {platform.python_version()}'
        case 'ls':
            try:
                root = get_workspace_root()
                directory = resolve_workspace_path(path, root)
                if directory == root and not root.exists():
                    return '（空目录）'
                entries = sorted(child.name for child in directory.iterdir() if not is_link(child))
            except (OSError, ValueError, RuntimeError):
                return '无法列出目录，请检查给出的工作区路径。'
            return '\n'.join(entries) or '（空目录）'

def get_tools():
    return [get_current_time, run_command]
