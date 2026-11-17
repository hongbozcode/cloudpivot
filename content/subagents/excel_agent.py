from connection import llm
from content.mcps import excel_mcp
from content.middleware import file_middle, wait_rate_limit


def get_agent():
    return {
        "name": "excel-agent",
        "description": "读取、创建及编辑 Excel 工作簿、工作表和单元格。",
        "system_prompt": "你是 Excel 助手。使用 Excel 工具操作工作簿，并使用工作区内的文件路径。",
        "middleware": [file_middle.FileMiddle(), wait_rate_limit.wait_rate_limit],
        "tools": excel_mcp.get_tools(),
        "model": llm.get_llm(),
    }
