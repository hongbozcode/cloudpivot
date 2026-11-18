import asyncio
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.types import CallToolResult, TextContent

from core import config
from utils.doc_utils.os_utils import resolve_workspace_path


def get_tools():
    root = Path(config.ROOT_PATH_AGENT).resolve()
    root.mkdir(parents=True, exist_ok=True)

    def resolve_path_arguments(arguments):
        return {
            name: str(resolve_workspace_path(value, root))
            if value is not None and name.endswith('path') else value
            for name, value in arguments.items()
        }

    async def resolve_paths(request, handler):
        try:
            args = await asyncio.to_thread(resolve_path_arguments, request.args)
        except (TypeError, ValueError, OSError, RuntimeError):
            return CallToolResult(
                isError=True,
                content=[TextContent(type='text', text='Excel 文件路径必须位于工作目录内。')],
            )
        return await handler(request.override(args=args))

    client = MultiServerMCPClient(
        {
            "excel": {
                "command": "uvx",
                "args": [
                    "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
                    "excel-mcp-server==2.3.2", "stdio", "--allow-dir", str(root),
                ],
                "cwd": str(root),
                "transport": "stdio",
            }
        },
        tool_interceptors=[resolve_paths],
    )
    return asyncio.run(asyncio.wait_for(client.get_tools(), timeout=90))
