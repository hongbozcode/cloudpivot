import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from api.accounts import CurrentAccount
from api.dependencies import Runtime, require_write_origin
from connection.mcp_connections import ApprovalMode, ToolListChanged
from connection.public_models import validate_public_https_url
from connection.remote_mcp import RemoteMcpUnavailable, discover_tools


router = APIRouter(prefix='/api/mcp/connections', dependencies=[Depends(require_write_origin)])
CONNECTION_MISSING = 'MCP 连接不存在或无权访问。'
LIST_CHANGED = '服务地址或工具清单已更新，请重新加载设置。'
Address = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class ConnectionSettings(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r'^[^\x00]+$')]
    url: Address


class ToolSettings(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    enabled: bool
    approval: ApprovalMode = 'confirm'


class Tool(ToolSettings):
    title: str
    description: str


class Connection(ConnectionSettings):
    id: UUID
    catalog_id: UUID | None
    tools: list[Tool]


class UpdateTools(BaseModel):
    catalog_id: UUID
    tools: Annotated[list[ToolSettings], Field(max_length=200)]


async def public_url(url: str) -> str:
    try:
        return await asyncio.wait_for(asyncio.to_thread(validate_public_https_url, url, allow_query=True), timeout=5)
    except (ValueError, TimeoutError):
        raise HTTPException(422, '请输入可以解析到公网的 HTTPS MCP 服务地址。') from None


@router.get('', response_model=list[Connection])
async def connections(account: CurrentAccount, app: Runtime):
    return await app.mcp.connections(account.id)


@router.post('', status_code=201, response_model=Connection)
async def create_connection(body: ConnectionSettings, account: CurrentAccount, app: Runtime):
    return await app.mcp.create(account.id, body.name, await public_url(body.url))


@router.patch('/{connection_id}', response_model=Connection)
async def update_connection(connection_id: UUID, body: ConnectionSettings, account: CurrentAccount, app: Runtime):
    if await app.mcp.get(account.id, connection_id) is None:
        raise HTTPException(404, CONNECTION_MISSING)
    return await app.mcp.update(account.id, connection_id, body.name, await public_url(body.url))


@router.post('/{connection_id}/test', response_model=Connection)
async def test_connection(connection_id: UUID, account: CurrentAccount, app: Runtime):
    connection = await app.mcp.get(account.id, connection_id)
    if connection is None:
        raise HTTPException(404, CONNECTION_MISSING)
    try:
        tools = await discover_tools(connection['url'])
        return await app.mcp.save_discovery(account.id, connection_id, connection['url'], tools)
    except RemoteMcpUnavailable:
        raise HTTPException(502, '连接失败。请检查服务地址和 Streamable HTTP 服务状态后重试。') from None
    except ToolListChanged:
        raise HTTPException(409, LIST_CHANGED) from None


@router.patch('/{connection_id}/tools', response_model=Connection)
async def update_tools(connection_id: UUID, body: UpdateTools, account: CurrentAccount, app: Runtime):
    settings = {
        tool.name: {'enabled': tool.enabled, 'approval': tool.approval if tool.enabled else 'confirm'}
        for tool in body.tools
    }
    if len(settings) != len(body.tools):
        raise HTTPException(422, '请为每个工具提交一项设置。')
    try:
        updated = await app.mcp.save_tools(account.id, connection_id, body.catalog_id, settings)
    except ToolListChanged:
        raise HTTPException(409, LIST_CHANGED) from None
    if updated is None:
        raise HTTPException(404, CONNECTION_MISSING)
    return updated
