import datetime
import subprocess

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
    """
    执行系统运维类终端命令(shell终端),不要使用cmd命令

    禁止事项(违反时必须改用对应工具,而不是调用本工具):
    - 读取任何文件内容 -> 必须使用 get_file_content 工具
    - 转换文件格式(转 pdf/docx/html/md 等) -> 必须使用 convert_file 工具
    - 查看当前时间 -> 必须使用 get_current_time 工具
    - 创建、写入、编辑文件 -> 本工具不支持,请告知用户改用文件写入工具

    本工具不会返回命令的退出状态之外的诊断信息,命令写错时只会得到错误输出文本。
    优先级最低:凡是其他工具能完成的任务,一律禁止调用本工具。
    :param command: 要执行的单条命令(str)
    :return: 包含退出码、标准输出、错误输出的文本(str)
    """
    try:
        process = subprocess.Popen(
            command, shell=True,
            cwd=config.ROOT_PATH_AGENT,  # 与文件工具共用同一工作目录,保证相对路径一致
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
        )
        stdout, stderr = process.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return "命令执行超时(120秒),已被强制终止。请勿重复执行同一条命令。"
    except FileNotFoundError:
        return f"工作目录不存在:{config.ROOT_PATH_AGENT},命令未执行。"

    return (
        f"退出码:{process.returncode}\n"
        f"正常输出:{stdout}\n"
        f"错误输出:{stderr}"
    )

def get_tools():
    return [get_current_time, run_command]