import asyncio
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from connection.schema import chat_run_events, chat_runs, tool_approvals


Decision = Literal['approved', 'rejected']


class ApprovalClosed(Exception):
    pass


class ToolApprovals:
    def __init__(self, database: AsyncEngine):
        self.database = database

    async def request(self, run_id: UUID, tool_id: UUID, preview: dict) -> bool:
        async with self.database.begin() as conn:
            if await _locked_run_status(conn, run_id) != 'running':
                return False
            await conn.execute(insert(tool_approvals).values(id=tool_id, run_id=run_id))
            await _publish(conn, run_id, tool_id, 'pending', **preview)
            return True

    async def wait(self, run_id: UUID, tool_id: UUID) -> bool:
        while True:
            async with self.database.connect() as conn:
                status = (await conn.execute(_approval_status(run_id, tool_id))).scalar_one()
            if status != 'pending':
                return status == 'approved'
            await asyncio.sleep(0.25)

    async def decide(
        self, run_id: UUID, tool_id: UUID, decision: Decision, *, stopping: Callable[[], bool],
    ) -> Decision | None:
        async with self.database.begin() as conn:
            run_status = await _locked_run_status(conn, run_id)
            status = (await conn.execute(_approval_status(run_id, tool_id))).scalar_one_or_none()
            if status is None:
                return None
            if status == decision:
                return decision
            if run_status != 'running' or status != 'pending' or stopping():
                raise ApprovalClosed
            await conn.execute(update(tool_approvals).where(tool_approvals.c.id == tool_id).values(status=decision))
            await _publish(conn, run_id, tool_id, decision)
            return decision


async def cancel_pending_approvals(conn: AsyncConnection, run_id: UUID) -> None:
    cancelled = await conn.execute(update(tool_approvals).where(
        tool_approvals.c.run_id == run_id, tool_approvals.c.status == 'pending',
    ).values(status='cancelled').returning(tool_approvals.c.id))
    for tool_id in cancelled.scalars():
        await _publish(conn, run_id, tool_id, 'cancelled')


async def _locked_run_status(conn: AsyncConnection, run_id: UUID) -> str | None:
    result = await conn.execute(select(chat_runs.c.status).where(chat_runs.c.id == run_id).with_for_update())
    return result.scalar_one_or_none()


def _approval_status(run_id: UUID, tool_id: UUID):
    return select(tool_approvals.c.status).where(tool_approvals.c.id == tool_id, tool_approvals.c.run_id == run_id)


async def _publish(conn: AsyncConnection, run_id: UUID, tool_id: UUID, status: str, **preview) -> None:
    await conn.execute(insert(chat_run_events).values(run_id=run_id, data={
        'type': 'tool_approval', 'tool_id': str(tool_id), 'status': status, **preview,
    }))
