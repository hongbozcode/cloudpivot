from langchain_core.language_models import BaseChatModel
from content.mcps import excel_mcp
from content.middleware import file_middle, wait_rate_limit
from content.middleware.run_actions import RunActionsMiddleware


def get_agent(model: BaseChatModel):
    return {
        "name": "excel-agent",
        "description": "读取、创建及编辑 Excel 工作簿、工作表和单元格。",
        "system_prompt": (
            "你是 Excel 助手。"
            "新建工作簿时直接使用目标路径，例如 /示例数据.xlsx。"
            "需要定位已有工作簿时，使用 list_workbooks。"
        ),
        "middleware": [file_middle.FileMiddle(), wait_rate_limit.wait_rate_limit, RunActionsMiddleware()],
        "tools": excel_mcp.get_tools(),
        "model": model,
    }
