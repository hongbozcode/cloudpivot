from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from connection.schema import mcp_connections


ApprovalMode = Literal['confirm', 'automatic']


@dataclass(frozen=True)
class RemoteTool:
    service: str
    url: str = field(repr=False)
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    approval: ApprovalMode
    connection_id: UUID


class ToolListChanged(Exception):
    pass


class McpConnections:
    def __init__(self, database: AsyncEngine):
        self.database = database

    async def connections(self, account_id: UUID) -> list[dict]:
        async with self.database.connect() as conn:
            rows = await conn.execute(select(mcp_connections).where(
                mcp_connections.c.account_id == account_id,
            ).order_by(mcp_connections.c.name, mcp_connections.c.id))
            return [dict(row) for row in rows.mappings()]

    async def get(self, account_id: UUID, connection_id: UUID) -> dict | None:
        async with self.database.connect() as conn:
            return await self._find(conn, account_id, connection_id)

    async def create(self, account_id: UUID, name: str, url: str) -> dict:
        async with self.database.begin() as conn:
            row = await conn.execute(insert(mcp_connections).values(
                id=uuid4(), account_id=account_id, name=name, url=url,
            ).returning(mcp_connections))
            return dict(row.mappings().one())

    async def update(self, account_id: UUID, connection_id: UUID, name: str, url: str) -> dict | None:
        async with self.database.begin() as conn:
            current = await self._find(conn, account_id, connection_id, lock=True)
            if current is None:
                return None
            values = {'name': name, 'url': url}
            if current['url'] != url:
                values.update(catalog_id=None, tools=[])
            return await self._write(conn, connection_id, values)

    async def save_discovery(
        self, account_id: UUID, connection_id: UUID, url: str, tools: list[dict],
    ) -> dict | None:
        async with self.database.begin() as conn:
            current = await self._find(conn, account_id, connection_id, lock=True)
            if current is None:
                return None
            if current['url'] != url:
                raise ToolListChanged
            previous = {tool['name']: tool for tool in current['tools']}
            discovered = []
            for tool in tools:
                saved = previous.get(tool['name'])
                # A changed input contract must be reviewed before the tool is used again.
                if saved is not None and saved['input_schema'] == tool['input_schema']:
                    tool = {**tool, 'enabled': saved['enabled'], 'approval': saved['approval']}
                discovered.append(tool)
            catalog_id = current['catalog_id'] if current['tools'] == discovered else None
            return await self._write(conn, connection_id, {'tools': discovered, 'catalog_id': catalog_id or uuid4()})

    async def save_tools(
        self, account_id: UUID, connection_id: UUID, catalog_id: UUID, settings: dict[str, dict],
    ) -> dict | None:
        async with self.database.begin() as conn:
            current = await self._find(conn, account_id, connection_id, lock=True)
            if current is None:
                return None
            tools = {tool['name']: tool for tool in current['tools']}
            if catalog_id != current['catalog_id'] or settings.keys() - tools.keys():
                raise ToolListChanged
            return await self._write(conn, connection_id, {
                'tools': [{**tool, **settings.get(name, {})} for name, tool in tools.items()],
            })

    async def _find(
        self, conn: AsyncConnection, account_id: UUID, connection_id: UUID, *, lock: bool = False,
    ) -> dict | None:
        lookup = select(mcp_connections).where(
            mcp_connections.c.id == connection_id, mcp_connections.c.account_id == account_id,
        )
        row = (await conn.execute(lookup.with_for_update() if lock else lookup)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def _write(self, conn: AsyncConnection, connection_id: UUID, values: dict) -> dict:
        result = await conn.execute(update(mcp_connections).where(
            mcp_connections.c.id == connection_id,
        ).values(**values).returning(mcp_connections))
        return dict(result.mappings().one())


async def enabled_remote_tools(conn: AsyncConnection, account_id: UUID) -> tuple[RemoteTool, ...]:
    rows = await conn.execute(select(mcp_connections).where(
        mcp_connections.c.account_id == account_id, mcp_connections.c.catalog_id.is_not(None),
    ).order_by(mcp_connections.c.id))
    return tuple(
        RemoteTool(
            row.name, row.url, tool['name'], tool['title'], tool['description'],
            tool['input_schema'], tool['approval'], row.id,
        )
        for row in rows for tool in row.tools if tool['enabled']
    )
