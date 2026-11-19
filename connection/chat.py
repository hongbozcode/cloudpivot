from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, insert, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine

from connection.schema import chat_messages, chat_run_events, chat_runs, chat_sessions


class SessionArchived(Exception):
    pass


class SessionRunning(Exception):
    pass


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

    async def run_for_account(self, account_id: UUID, run_id: UUID) -> dict | None:
        async with self.database.connect() as conn:
            row = (await conn.execute(
                select(chat_runs).select_from(chat_runs.join(chat_sessions))
                .where(chat_runs.c.id == run_id, chat_sessions.c.account_id == account_id)
            )).mappings().one_or_none()
            return dict(row) if row else None

    async def begin(self, account_id: UUID, session_id: UUID, text: str) -> tuple[dict, bool] | None:
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
            active = (await conn.execute(
                select(chat_runs).where(chat_runs.c.session_id == session_id, chat_runs.c.status == 'running')
            )).mappings().one_or_none()
            if active:
                return dict(active), False
            run_id = uuid4()
            inserted = await conn.execute(insert(chat_runs).values(id=run_id, session_id=session_id).returning(chat_runs))
            await conn.execute(insert(chat_messages).values(session_id=session_id, run_id=run_id, role='user', text=text))
            await conn.execute(update(chat_sessions).where(chat_sessions.c.id == session_id).values(updated_at=func.now()))
            return dict(inserted.mappings().one()), True

    async def append_text(self, run_id: UUID, text: str) -> None:
        async with self.database.begin() as conn:
            await conn.execute(insert(chat_run_events).values(run_id=run_id, data={'type': 'text_delta', 'text': text}))

    async def finish(
        self, run_id: UUID, status: Literal['completed', 'failed', 'interrupted'], answer: str = '',
    ) -> None:
        async with self.database.begin() as conn:
            session_id = (await conn.execute(
                update(chat_runs).where(chat_runs.c.id == run_id, chat_runs.c.status == 'running')
                .values(status=status, finished_at=func.now()).returning(chat_runs.c.session_id)
            )).scalar_one_or_none()
            if session_id is None:
                return
            if status == 'completed':
                await conn.execute(insert(chat_messages).values(
                    session_id=session_id, run_id=run_id, role='assistant', text=answer,
                ))
            await conn.execute(insert(chat_run_events).values(run_id=run_id, data={'type': 'done', 'status': status}))

    async def interrupt_pending(self) -> None:
        interrupted = (
            update(chat_runs).where(chat_runs.c.status == 'running')
            .values(status='interrupted', finished_at=func.now()).returning(chat_runs.c.id).cte('interrupted')
        )
        async with self.database.begin() as conn:
            await conn.execute(insert(chat_run_events).from_select(
                ['run_id', 'data'],
                select(interrupted.c.id, literal({'type': 'done', 'status': 'interrupted'}, type_=JSONB)),
            ))

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
