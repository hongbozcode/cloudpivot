import datetime
import subprocess
from sys import stdout, stderr

from langchain.tools import tool

from core import config


@ tool
def get_current_time():
    """
    获取当前时间
    :return: 当前时间
    """
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@tool
def run_command(command: str):
    """运行终端命令。注意：运行在真实操作系统中，工作目录为共享工作目录。
    命令失败时错误信息会如实返回，请根据错误信息判断，不要盲目重试。"""
    try:
        process = subprocess.run(command, shell=True, capture_output=True,
                                 text=True, timeout=120, cwd=config.ROOT_PATH_AGENT)
        return f"退出码: {process.returncode}\n正常输出: {stdout}\n错误输出: {stderr}"
    except subprocess.TimeoutExpired:
        return "命令超时（120秒），已终止"

def get_tools():
    return [get_current_time, run_command]