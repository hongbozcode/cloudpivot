from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, insert, literal, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from connection.schema import chat_messages, chat_run_events, chat_runs, chat_sessions


class SessionArchived(Exception):
    pass


class SessionRunning(Exception):
    pass


class RunNotRetryable(Exception):
    pass


TerminalStatus = Literal['completed', 'failed', 'interrupted', 'stopped']
RUN_ERRORS = {
    'failed': {'code': 'run_failed', 'message': '本次运行失败，请重试。'},
    'interrupted': {'code': 'run_interrupted', 'message': '本次运行已中断，可以重试原任务。'},
}


@dataclass
class CompletedContext:
    last_completed: UUID | None
    legacy_checkpoint_is_current: bool
    messages: list[dict]


_session_columns = (chat_sessions.c.id, chat_sessions.c.title, chat_sessions.c.updated_at, chat_sessions.c.archived_at)
_latest_run = select(chat_runs).order_by(chat_runs.c.started_at.desc(), chat_runs.c.id.desc()).limit(1)


class ChatStore:
    def __init__(self, database: AsyncEngine):
        self.database = database

    async def create_session(self, account_id: UUID) -> dict:
        async with self.database.begin() as conn:
            result = await conn.execute(
                insert(chat_sessions).values(id=uuid4(), account_id=account_id).returning(*_session_columns)
            )
            return dict(result.mappings().one())

    async def sessions(self, account_id: UUID) -> list[dict]:
        async with self.database.connect() as conn:
            result = await conn.execute(
                select(*_session_columns)
                .where(chat_sessions.c.account_id == account_id, chat_sessions.c.archived_at.is_(None))
                .order_by(chat_sessions.c.updated_at.desc(), chat_sessions.c.id)
            )
            return [dict(row) for row in result.mappings()]

    async def session(self, account_id: UUID, session_id: UUID) -> dict | None:
        async with self.database.connect() as conn:
            row = (await conn.execute(
                select(*_session_columns).where(chat_sessions.c.account_id == account_id, chat_sessions.c.id == session_id)
            )).mappings().one_or_none()
            return dict(row) if row else None

    async def rename(self, account_id: UUID, session_id: UUID, title: str) -> dict | None:
        async with self.database.begin() as conn:
            row = (await conn.execute(
                update(chat_sessions).where(
                    chat_sessions.c.account_id == account_id, chat_sessions.c.id == session_id,
                    chat_sessions.c.archived_at.is_(None),
                ).values(title=title, updated_at=func.now()).returning(*_session_columns)
            )).mappings().one_or_none()
            return dict(row) if row else None

    async def archive(self, account_id: UUID, session_id: UUID) -> dict | None:
        async with self.database.begin() as conn:
            session = (await conn.execute(
                select(chat_sessions.c.id)
                .where(chat_sessions.c.account_id == account_id, chat_sessions.c.id == session_id)
                .with_for_update()
            )).scalar_one_or_none()
            if session is None:
                return None
            running = (await conn.execute(
                select(chat_runs.c.id).where(chat_runs.c.session_id == session_id, chat_runs.c.status == 'running')
            )).scalar_one_or_none()
            if running:
                raise SessionRunning
            result = await conn.execute(
                update(chat_sessions).where(chat_sessions.c.id == session_id)
                .values(archived_at=func.coalesce(chat_sessions.c.archived_at, func.now()))
                .returning(*_session_columns)
            )
            return dict(result.mappings().one())

    async def messages(self, session_id: UUID) -> list[dict]:
        async with self.database.connect() as conn:
            result = await conn.execute(
                select(chat_messages).where(chat_messages.c.session_id == session_id).order_by(chat_messages.c.id)
            )
            return [dict(row) for row in result.mappings()]

    async def latest_run(self, session_id: UUID) -> dict | None:
        async with self.database.connect() as conn:
            row = (await conn.execute(
                _latest_run.where(chat_runs.c.session_id == session_id)
            )).mappings().one_or_none()
            return dict(row) if row else None

    async def session_runs(self, session_id: UUID) -> list[dict]:
        async with self.database.connect() as conn:
            result = await conn.execute(select(chat_runs).where(
                chat_runs.c.session_id == session_id,
            ).order_by(chat_runs.c.started_at, chat_runs.c.id))
            runs = {row['id']: dict(row, artifacts={}) for row in result.mappings()}
            artifacts = await conn.execute(select(chat_run_events.c.run_id, chat_run_events.c.data)
                .select_from(chat_run_events.join(chat_runs)).where(
                    chat_runs.c.session_id == session_id, chat_run_events.c.data['type'].astext == 'artifact',
                ).order_by(chat_run_events.c.id))
            for run_id, data in artifacts:
                if run_id in runs:
                    runs[run_id]['artifacts'][data['artifact_id']] = data
            return [dict(run, artifacts=list(run['artifacts'].values())) for run in runs.values()]

    async def completed_context(self, session_id: UUID) -> CompletedContext:
        async with self.database.connect() as conn:
            completed = chat_runs.c.session_id == session_id, chat_runs.c.status == 'completed'
            last_completed = (await conn.execute(_latest_run.with_only_columns(chat_runs.c.id).where(
                *completed,
            ))).scalar_one_or_none()
            latest_finished = (await conn.execute(_latest_run.with_only_columns(chat_runs.c.id).where(
                chat_runs.c.session_id == session_id, chat_runs.c.status != 'running',
            ))).scalar_one_or_none()
            history = await conn.execute(select(chat_messages.c.role, chat_messages.c.text)
                .select_from(chat_messages.join(chat_runs, chat_messages.c.run_id == chat_runs.c.id))
                .where(*completed).order_by(chat_messages.c.id))
            return CompletedContext(last_completed, last_completed == latest_finished, [
                dict(row) for row in history.mappings()
            ])

    async def is_running(self, run_id: UUID) -> bool:
        async with self.database.connect() as conn:
            return (await conn.execute(select(chat_runs.c.status).where(
                chat_runs.c.id == run_id,
            ))).scalar_one_or_none() == 'running'

    async def run_for_account(self, account_id: UUID, run_id: UUID) -> dict | None:
        async with self.database.connect() as conn:
            row = (await conn.execute(
                select(chat_runs).select_from(chat_runs.join(chat_sessions))
                .where(chat_runs.c.id == run_id, chat_sessions.c.account_id == account_id)
            )).mappings().one_or_none()
            return dict(row) if row else None

    async def begin(
        self, account_id: UUID, session_id: UUID, text: str, *, retry_of: UUID | None = None,
    ) -> tuple[dict, bool, str] | None:
        async with self.database.begin() as conn:
            session = (await conn.execute(
                select(chat_sessions.c.archived_at)
                .where(chat_sessions.c.account_id == account_id, chat_sessions.c.id == session_id)
                .with_for_update()
            )).one_or_none()
            if session is None:
                return None
            if session.archived_at is not None:
                raise SessionArchived
            if retry_of is not None:
                prompt = (await conn.execute(
                    select(chat_messages.c.text)
                    .select_from(chat_messages.join(chat_runs, chat_messages.c.run_id == chat_runs.c.id))
                    .where(
                        chat_runs.c.session_id == session_id, chat_runs.c.id == retry_of,
                        chat_runs.c.status.in_(('failed', 'interrupted', 'stopped')),
                        chat_messages.c.role == 'user',
                    ),
                )).scalar_one_or_none()
                if prompt is None:
                    raise RunNotRetryable
                text = prompt
            active = (await conn.execute(
                select(chat_runs).where(chat_runs.c.session_id == session_id, chat_runs.c.status == 'running')
            )).mappings().one_or_none()
            if active:
                return dict(active), False, text
            run_id = uuid4()
            inserted = await conn.execute(insert(chat_runs).values(id=run_id, session_id=session_id).returning(chat_runs))
            await conn.execute(insert(chat_messages).values(session_id=session_id, run_id=run_id, role='user', text=text))
            await conn.execute(update(chat_sessions).where(chat_sessions.c.id == session_id).values(updated_at=func.now()))
            return dict(inserted.mappings().one()), True, text

    async def append_event(self, run_id: UUID, data: dict) -> None:
        async with self.database.begin() as conn:
            active = (await conn.execute(select(chat_runs.c.id).where(
                chat_runs.c.id == run_id, chat_runs.c.status == 'running',
            ).with_for_update())).scalar_one_or_none()
            if active is not None:
                await conn.execute(insert(chat_run_events).values(run_id=run_id, data=data))

    async def finish(
        self, run_id: UUID, status: TerminalStatus,
    ) -> None:
        async with self.database.begin() as conn:
            await self._finish(conn, status, run_id=run_id)

    async def interrupt_pending(self) -> None:
        async with self.database.begin() as conn:
            await self._finish(conn, 'interrupted')

    async def _finish(self, conn: AsyncConnection, status: TerminalStatus, *, run_id: UUID | None = None) -> None:
        changed = update(chat_runs).where(chat_runs.c.status == 'running')
        if run_id is not None:
            changed = changed.where(chat_runs.c.id == run_id)
        runs = (await conn.execute(changed.values(status=status, finished_at=func.now())
            .returning(chat_runs.c.id, chat_runs.c.session_id, chat_runs.c.finished_at))).mappings()
        event_text = chat_run_events.c.data['text'].astext
        for run in runs:
            answer = select(
                literal(run['session_id']), literal(run['id']), literal('assistant'),
                func.string_agg(event_text, aggregate_order_by(literal(''), chat_run_events.c.id)),
            ).where(
                chat_run_events.c.run_id == run['id'], chat_run_events.c.data['type'].astext == 'text_delta',
            ).having(func.btrim(func.string_agg(event_text, literal(''))) != '')
            await conn.execute(insert(chat_messages).from_select(['session_id', 'run_id', 'role', 'text'], answer))
            await conn.execute(insert(chat_run_events).values(run_id=run['id'], data={
                'type': 'done', 'status': status, 'finished_at': run['finished_at'].isoformat(),
                'error': RUN_ERRORS.get(status),
            }))

    async def artifact_name(self, run_id: UUID, artifact_id: UUID) -> str | None:
        data = chat_run_events.c.data
        async with self.database.connect() as conn:
            return (await conn.execute(select(data['name'].astext).where(
                chat_run_events.c.run_id == run_id, data['type'].astext == 'artifact',
                data['artifact_id'].astext == str(artifact_id),
            ).order_by(chat_run_events.c.id.desc()).limit(1))).scalar_one_or_none()

    async def events(self, run_id: UUID, after: int, limit: int) -> tuple[bool, list[dict]]:
        async with self.database.connect() as conn:
            # Read status before events because both completion and its last event
            # become visible in the same transaction.
            status = (await conn.execute(select(chat_runs.c.status).where(chat_runs.c.id == run_id))).scalar_one_or_none()
            records = await conn.execute(
                select(chat_run_events.c.id, chat_run_events.c.data)
                .where(chat_run_events.c.run_id == run_id, chat_run_events.c.id > after)
                .order_by(chat_run_events.c.id).limit(limit)
            )
            return status != 'running', [dict(row) for row in records.mappings()]
