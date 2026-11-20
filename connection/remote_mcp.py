import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, PaginatedRequestParams

from connection.public_models import AsyncPublicTransport


class RemoteMcpUnavailable(Exception):
    pass


@asynccontextmanager
async def _session(url: str, timeout: float) -> AsyncIterator[ClientSession]:
    try:
        async with asyncio.timeout(timeout), httpx.AsyncClient(
            transport=AsyncPublicTransport(httpx.URL(url)), trust_env=False,
            timeout=httpx.Timeout(timeout, connect=5),
        ) as client:
            async with streamable_http_client(url, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
    except Exception as exc:
        raise RemoteMcpUnavailable from exc


async def discover_tools(url: str) -> list[dict]:
    async with _session(url, 15) as session:
        capabilities = session.get_server_capabilities()
        if capabilities is None or capabilities.tools is None:
            return []
        tools: dict[str, dict] = {}
        seen_cursors: set[str] = set()
        cursor = None
        while True:
            page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
            for tool in page.tools:
                encoded_schema = json.dumps(tool.inputSchema)
                if (not tool.name or not tool.name.isprintable() or len(tool.name) > 256
                        or len(encoded_schema) > 32_768 or r'\u0000' in encoded_schema):
                    raise RemoteMcpUnavailable
                tools[tool.name] = {
                    'name': tool.name,
                    'title': (tool.title or (tool.annotations and tool.annotations.title) or tool.name)
                    .replace('\0', '')[:256] or tool.name,
                    'description': (tool.description or '').replace('\0', '')[:2000],
                    'input_schema': tool.inputSchema,
                    'enabled': False,
                    'approval': 'confirm',
                }
            cursor = page.nextCursor
            if len(tools) > 200 or len(seen_cursors) >= 20 or cursor in seen_cursors:
                raise RemoteMcpUnavailable
            if cursor is None:
                return list(tools.values())
            seen_cursors.add(cursor)


async def call_tool(url: str, name: str, arguments: dict[str, Any]) -> CallToolResult:
    async with _session(url, 25) as session:
        return await session.call_tool(name, arguments)
