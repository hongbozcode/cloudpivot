from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from connection.schema import metadata


@asynccontextmanager
async def open_database(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        'postgresql+psycopg://',
        connect_args={'connect_timeout': 5, **conninfo_to_dict(database_url)},
        pool_size=8, max_overflow=0, pool_pre_ping=True, pool_timeout=5, hide_parameters=True,
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text('SELECT pg_advisory_xact_lock(1594950899)'))
            await conn.run_sync(metadata.create_all)
            await conn.execute(text('ALTER TABLE session_files ADD COLUMN IF NOT EXISTS workspace_key text'))
            await conn.execute(text('ALTER TABLE chat_runs ADD COLUMN IF NOT EXISTS uses_platform_model boolean NOT NULL DEFAULT true'))
            constraint = (await conn.execute(text("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'chat_runs'::regclass AND conname = 'chat_run_status'
            """))).scalar_one()
            if "'stopped'" not in constraint:
                await conn.execute(text("""
                    ALTER TABLE chat_runs DROP CONSTRAINT chat_run_status,
                    ADD CONSTRAINT chat_run_status
                    CHECK (status IN ('running', 'completed', 'failed', 'interrupted', 'stopped'))
                """))
        yield engine
    finally:
        await engine.dispose()


CheckpointPool = AsyncConnectionPool[AsyncConnection[DictRow]]


@asynccontextmanager
async def open_checkpoint_pool(database_url: str) -> AsyncIterator[CheckpointPool]:
    pool = AsyncConnectionPool[AsyncConnection[DictRow]](
        database_url,
        kwargs={'row_factory': dict_row, 'autocommit': True, 'connect_timeout': 5, 'prepare_threshold': 0},
        min_size=1, max_size=4, timeout=5, open=False,
    )
    try:
        await pool.open(wait=True, timeout=10)
        yield pool
    finally:
        await pool.close()
