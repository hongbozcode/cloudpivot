import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints

from api.accounts import CurrentAccount
from api.dependencies import Runtime, require_write_origin
from connection.chat import SessionArchived, SessionRunning
from connection.run_cache import PAGE_SIZE


router = APIRouter(prefix='/api', dependencies=[Depends(require_write_origin)])
SESSION_MISSING = '会话不存在或无权访问。'


class Session(BaseModel):
    id: UUID
    title: str
    updated_at: datetime
    archived_at: datetime | None


class Run(BaseModel):
    id: UUID
    session_id: UUID
    status: Literal['running', 'completed', 'failed', 'interrupted']
    started_at: datetime
    finished_at: datetime | None


class StartedRun(Run):
    created: bool


class SessionDetail(Session):
    latest_run: Run | None


class Message(BaseModel):
    id: int
    run_id: UUID
    role: Literal['user', 'assistant']
    text: str
    created_at: datetime


class RenameSession(BaseModel):
    title: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1, max_length=100, pattern=r'^[^\x00]+$',
    )]


class StartRun(BaseModel):
    text: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1, max_length=20_000, pattern=r'^[^\x00]+$',
    )]


@router.get('/sessions', response_model=list[Session])
async def list_sessions(account: CurrentAccount, app: Runtime) -> list[dict]:
    return await app.chat.sessions(account.id)


@router.post('/sessions', status_code=201, response_model=Session)
async def create_session(account: CurrentAccount, app: Runtime) -> dict:
    return await app.chat.create_session(account.id)


@router.get('/sessions/{session_id}', response_model=SessionDetail)
async def get_session(session_id: UUID, account: CurrentAccount, app: Runtime) -> dict:
    row = await app.chat.session(account.id, session_id)
    if row is None:
        raise HTTPException(404, SESSION_MISSING)
    return {**row, 'latest_run': await app.chat.latest_run(session_id)}


@router.patch('/sessions/{session_id}', response_model=Session)
async def rename_session(session_id: UUID, body: RenameSession, account: CurrentAccount, app: Runtime) -> dict:
    row = await app.chat.rename(account.id, session_id, body.title)
    if row is None:
        raise HTTPException(404, SESSION_MISSING)
    return row


@router.post('/sessions/{session_id}/archive', response_model=Session)
async def archive_session(session_id: UUID, account: CurrentAccount, app: Runtime) -> dict:
    try:
        row = await app.chat.archive(account.id, session_id)
    except SessionRunning as exc:
        raise HTTPException(409, '请等当前运行结束后再归档。') from exc
    if row is None:
        raise HTTPException(404, SESSION_MISSING)
    return row


@router.get('/sessions/{session_id}/messages', response_model=list[Message])
async def get_messages(session_id: UUID, account: CurrentAccount, app: Runtime) -> list[dict]:
    if await app.chat.session(account.id, session_id) is None:
        raise HTTPException(404, SESSION_MISSING)
    return await app.chat.messages(session_id)


@router.post('/sessions/{session_id}/runs', status_code=202, response_model=StartedRun)
async def start_run(session_id: UUID, body: StartRun, account: CurrentAccount, app: Runtime) -> dict:
    try:
        result = await app.chat.begin(account.id, session_id, body.text)
    except SessionArchived as exc:
        raise HTTPException(409, '会话已归档。请新建会话后发送。') from exc
    if result is None:
        raise HTTPException(404, SESSION_MISSING)
    run, created = result
    if created:
        app.runs.start(run['id'], session_id, body.text)
    return {**run, 'created': created}


@router.get('/runs/{run_id}/events', response_class=StreamingResponse)
async def stream_run(
    run_id: UUID, account: CurrentAccount, app: Runtime,
    after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
) -> Response:
    if await app.chat.run_for_account(account.id, run_id) is None:
        raise HTTPException(404, '运行不存在或无权访问。')

    async def stream() -> AsyncIterator[str]:
        cursor = after
        clock = asyncio.get_running_loop()
        heartbeat_at = clock.time() + 10
        # Send a comment large enough to flush small initial responses through browsers and proxies.
        yield ': connected' + (' ' * 2048) + '\n\n'
        while True:
            finished, events = await app.events.page(run_id, cursor)
            for row in events:
                cursor = row['id']
                data = json.dumps(row['data'], ensure_ascii=False)
                yield f'id: {cursor}\ndata: {data}\n\n'
            if len(events) == PAGE_SIZE:
                continue
            if finished:
                return
            if clock.time() >= heartbeat_at:
                yield ': waiting\n\n'
                heartbeat_at = clock.time() + 10
            await asyncio.sleep(0.15)

    return StreamingResponse(stream(), media_type='text/event-stream', headers={
        'Cache-Control': 'no-store, no-transform', 'X-Accel-Buffering': 'no',
        'X-Content-Type-Options': 'nosniff',
    })
