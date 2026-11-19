import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints, computed_field

from api.accounts import CurrentAccount
from api.dependencies import Runtime, require_write_origin
from connection.chat import RUN_ERRORS, InvalidAttachments, RunNotRetryable, SessionArchived, SessionRunning
from connection.platform import DailyLimitReached
from connection.run_cache import PAGE_SIZE
from content.run_artifacts import artifact_path


router = APIRouter(prefix='/api', dependencies=[Depends(require_write_origin)])
SESSION_MISSING = '会话不存在或无权访问。'
RUN_MISSING = '运行不存在或无权访问。'


class Session(BaseModel):
    id: UUID
    title: str
    updated_at: datetime
    archived_at: datetime | None


class Artifact(BaseModel):
    artifact_id: UUID
    name: str


class Run(BaseModel):
    id: UUID
    session_id: UUID
    status: Literal['running', 'completed', 'failed', 'interrupted', 'stopped']
    started_at: datetime
    finished_at: datetime | None
    artifacts: list[Artifact] = Field(default_factory=list)

    @computed_field
    @property
    def error(self) -> dict[str, str] | None:
        return RUN_ERRORS.get(self.status)


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
    attachment_ids: list[UUID] = Field(default_factory=list)


class RenameSession(BaseModel):
    title: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1, max_length=100, pattern=r'^[^\x00]+$',
    )]


class StartRun(BaseModel):
    text: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1, max_length=20_000, pattern=r'^[^\x00]+$',
    )]
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)


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
    return await _start_run(session_id, account, app, body.text, attachment_ids=list(dict.fromkeys(body.attachment_ids)))


@router.get('/sessions/{session_id}/runs', response_model=list[Run])
async def session_runs(session_id: UUID, account: CurrentAccount, app: Runtime) -> list[dict]:
    if await app.chat.session(account.id, session_id) is None:
        raise HTTPException(404, SESSION_MISSING)
    return await app.chat.session_runs(session_id)


async def _start_run(
    session_id: UUID, account: CurrentAccount, app: Runtime, text: str, *,
    attachment_ids: list[UUID] | None = None, retry_of: UUID | None = None,
) -> dict:
    try:
        result = await app.chat.begin(account.id, session_id, text, attachment_ids=attachment_ids or [], retry_of=retry_of)
    except SessionArchived as exc:
        raise HTTPException(409, '会话已归档。请新建会话后发送。') from exc
    except RunNotRetryable as exc:
        raise HTTPException(409, '当前运行不能重试。') from exc
    except InvalidAttachments as exc:
        raise HTTPException(422, '有附件不可用，请从当前会话重新添加。') from exc
    except DailyLimitReached as exc:
        raise HTTPException(429, f'今日已使用 {exc.used} 次平台模型运行，当前上限为 {exc.limit} 次。历史和文件仍可查看。') from exc
    if result is None:
        raise HTTPException(404, SESSION_MISSING)
    run, run_input = result
    if run_input is not None:
        app.runs.start(run['id'], session_id, run_input)
    return {**run, 'created': run_input is not None}


async def _owned_run(app: Runtime, account: CurrentAccount, run_id: UUID) -> dict:
    run = await app.chat.run_for_account(account.id, run_id)
    if run is None:
        raise HTTPException(404, RUN_MISSING)
    return run


@router.post('/runs/{run_id}/stop', response_model=Run)
async def stop_run(run_id: UUID, account: CurrentAccount, app: Runtime) -> dict:
    run = await _owned_run(app, account, run_id)
    if run['status'] == 'running':
        await app.runs.stop(run_id)
        run = await _owned_run(app, account, run_id)
    return run


@router.post('/runs/{run_id}/retry', status_code=202, response_model=StartedRun)
async def retry_run(run_id: UUID, account: CurrentAccount, app: Runtime) -> dict:
    run = await _owned_run(app, account, run_id)
    return await _start_run(run['session_id'], account, app, '', retry_of=run_id)


@router.get('/runs/{run_id}/artifacts/{artifact_id}', response_class=FileResponse)
async def download_artifact(run_id: UUID, artifact_id: UUID, account: CurrentAccount, app: Runtime) -> FileResponse:
    await _owned_run(app, account, run_id)
    name = await app.chat.artifact_name(run_id, artifact_id)
    try:
        path = artifact_path(run_id, artifact_id)
        if name and path.is_file():
            return FileResponse(path, filename=name, media_type='application/octet-stream', headers={
                'X-Content-Type-Options': 'nosniff',
            })
    except (OSError, ValueError):
        pass
    raise HTTPException(404, '产物不存在或无权访问。')


@router.get('/runs/{run_id}/events', response_class=StreamingResponse)
async def stream_run(
    run_id: UUID, account: CurrentAccount, app: Runtime,
    after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
) -> Response:
    await _owned_run(app, account, run_id)

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
