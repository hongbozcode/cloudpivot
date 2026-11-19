import asyncio
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from connection.schema import chat_run_events, chat_runs, chat_sessions, session_files
from content.session_files import WorkspaceFile


class ReadOnlyWorkspace(Exception):
    pass


_sources = select(session_files.c.id, session_files.c.name, session_files.c.workspace_key)
_event = chat_run_events.c.data
_artifacts = select(chat_run_events.c.id, chat_run_events.c.run_id, _event).select_from(chat_run_events.join(chat_runs)).where(
    _event['type'].astext == 'artifact',
)


async def attachment_files(conn: AsyncConnection, session_id: UUID, file_ids: list[UUID]) -> list[WorkspaceFile]:
    if not file_ids:
        return []
    rows = await conn.execute(_sources.where(session_files.c.session_id == session_id, session_files.c.id.in_(file_ids)))
    by_id = {file_id: WorkspaceFile(file_id, name, 'source', workspace_key=key) for file_id, name, key in rows}
    return [by_id[file_id] for file_id in file_ids if file_id in by_id]


class FileStore:
    def __init__(self, database: AsyncEngine):
        self.database = database

    async def upload(self, account_id: UUID, session_id: UUID, name: str, source: BinaryIO) -> WorkspaceFile | None:
        file = WorkspaceFile(uuid4(), name, 'source')
        paths: tuple[Path, ...] = ()
        try:
            async with self.database.begin() as conn:
                session = (await conn.execute(select(chat_sessions.c.archived_at).where(
                    chat_sessions.c.id == session_id, chat_sessions.c.account_id == account_id,
                ).with_for_update())).one_or_none()
                if session is None:
                    return None
                if session.archived_at is not None:
                    raise ReadOnlyWorkspace
                running = (await conn.execute(select(chat_runs.c.id).where(
                    chat_runs.c.session_id == session_id, chat_runs.c.status == 'running',
                ))).scalar_one_or_none()
                if running is not None:
                    raise ReadOnlyWorkspace
                saving = asyncio.create_task(asyncio.to_thread(file.save, session_id, source))
                try:
                    paths = await asyncio.shield(saving)
                except asyncio.CancelledError:
                    paths = await saving
                    raise
                await conn.execute(insert(session_files).values(id=file.id, session_id=session_id, name=name))
        except BaseException:
            for path in paths:
                await asyncio.to_thread(file.discard, path)
            raise
        return file

    async def list(self, session_id: UUID) -> list[WorkspaceFile]:
        async with self.database.connect() as conn:
            sources = await conn.execute(_sources.where(session_files.c.session_id == session_id).order_by(
                session_files.c.created_at, session_files.c.id,
            ))
            artifacts = await conn.execute(_artifacts.where(chat_runs.c.session_id == session_id).order_by(
                chat_run_events.c.id,
            ))
            outputs = {}
            for revision, run_id, event in artifacts:
                file_id = UUID(event['artifact_id'])
                outputs[file_id] = WorkspaceFile(file_id, event['name'], 'artifact', run_id, str(revision))
            return [
                *(WorkspaceFile(file_id, name, 'source', workspace_key=key) for file_id, name, key in sources),
                *outputs.values(),
            ]

    async def get(self, session_id: UUID, file_id: UUID) -> WorkspaceFile | None:
        async with self.database.connect() as conn:
            sources = await attachment_files(conn, session_id, [file_id])
            if sources:
                return sources[0]
            artifact = (await conn.execute(_artifacts.where(
                chat_runs.c.session_id == session_id, _event['artifact_id'].astext == str(file_id),
            ).order_by(chat_run_events.c.id.desc()).limit(1))).one_or_none()
            if artifact:
                revision, run_id, event = artifact
                return WorkspaceFile(file_id, event['name'], 'artifact', run_id, str(revision))
            return None
