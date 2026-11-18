import datetime
import platform

from langchain.tools import tool


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
    根据用户明确指定的命令查询运行环境。支持 pwd、uname -s 和 python --version。
    :param command: 一条受支持的查询命令
    """
    match command.strip().lower():
        case 'pwd':
            return '工作区路径：/'
        case 'uname -s':
            return platform.system()
        case 'python --version':
            return f'Python {platform.python_version()}'
        case _:
            return '请选择支持的查询：pwd、uname -s、python --version。'

def get_tools():
    return [get_current_time, run_command]
