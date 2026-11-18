import asyncio
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.types import CallToolResult, TextContent

from content.workspace import get_workspace_root
from utils.doc_utils.os_utils import resolve_workspace_path


def _client(root: Path, *, interceptors=None):
    return MultiServerMCPClient(
        {
            'excel': {
                'command': 'uvx',
                'args': [
                    '--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple',
                    'excel-mcp-server==2.3.2', 'stdio', '--allow-dir', str(root),
                ],
                'cwd': str(root),
                'transport': 'stdio',
            }
        },
        tool_interceptors=interceptors,
    )


def _prepare_call(arguments):
    root = get_workspace_root()
    args = {
        name: str(resolve_workspace_path(value, root))
        if value is not None and (name.endswith('path') or name == 'directory') else value
        for name, value in arguments.items()
    }
    root.mkdir(parents=True, exist_ok=True)
    return root, args


async def _call_mcp(root: Path, name: str, args: dict) -> CallToolResult:
    async with _client(root).session('excel') as session:
        return await session.call_tool(name, args)


def _call_windows_mcp(root: Path, name: str, args: dict) -> CallToolResult:
    # MCP 1.30 resolves uvx with synchronous filesystem calls on Windows.
    # Keep that SDK event loop outside LangGraph's blocking detector.
    result = subprocess.run(
        [sys.executable, '-m', 'content.mcps.excel_mcp'],
        cwd=Path(__file__).resolve().parents[2],
        input=json.dumps({'root': str(root), 'name': name, 'args': args}),
        encoding='utf-8',
        stdout=subprocess.PIPE,
        check=True,
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return CallToolResult.model_validate_json(result.stdout)


async def _call_in_workspace(request, _handler):
    try:
        root, args = await asyncio.to_thread(_prepare_call, request.args)
    except (TypeError, ValueError, OSError, RuntimeError):
        return CallToolResult(
            isError=True,
            content=[TextContent(type='text', text='Excel 文件路径必须位于当前会话工作目录内。')],
        )

    if sys.platform == 'win32':
        return await asyncio.to_thread(_call_windows_mcp, root, request.name, args)
    return await _call_mcp(root, request.name, args)


def get_tools():
    # Discover schemas in an empty workspace at startup. Calls use a separate
    # connection constructed for the active thread, including list_workbooks.
    with TemporaryDirectory(prefix='cloudpivot-excel-schema-') as schema_dir:
        client = _client(Path(schema_dir), interceptors=[_call_in_workspace])
        return asyncio.run(asyncio.wait_for(client.get_tools(), timeout=90))


if __name__ == '__main__':
    request = json.load(sys.stdin)
    result = asyncio.run(_call_mcp(Path(request['root']), request['name'], request['args']))
    sys.stdout.buffer.write(result.model_dump_json(by_alias=True).encode('utf-8'))
