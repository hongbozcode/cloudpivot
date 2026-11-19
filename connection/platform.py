from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from connection.schema import account_quotas, accounts, chat_runs, chat_sessions, platform_settings


DEFAULT_DAILY_LIMIT = 20
QUOTA_TIMEZONE = timezone(timedelta(hours=8))


class DailyLimitReached(Exception):
    def __init__(self, used: int, limit: int):
        self.used = used
        self.limit = limit


@dataclass(frozen=True)
class PlatformModels:
    llm: str | None
    vlm: str | None


_settings = select(platform_settings).where(platform_settings.c.id == 1)
_account_quotas = accounts.outerjoin(account_quotas)


def _usage(at: datetime):
    midnight = at.astimezone(QUOTA_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    return select(chat_sessions.c.account_id, func.count(chat_runs.c.id).label('used')).select_from(
        chat_sessions.join(chat_runs),
    ).where(chat_runs.c.started_at >= midnight, chat_runs.c.started_at <= at).group_by(chat_sessions.c.account_id)


class PlatformStore:
    def __init__(self, database: AsyncEngine, llms: tuple[str, ...], vlms: tuple[str, ...]):
        self.database = database
        self.llms = llms
        self.vlms = vlms

    def _models(self, row) -> PlatformModels:
        llm = row['llm'] if row else None
        vlm = row['vlm'] if row else None
        return PlatformModels(
            llm if llm in self.llms else next(iter(self.llms), None),
            vlm if vlm in self.vlms else next(iter(self.vlms), None),
        )

    async def models(self) -> PlatformModels:
        async with self.database.connect() as conn:
            return self._models((await conn.execute(_settings)).mappings().one_or_none())

    async def save_models(self, models: PlatformModels) -> None:
        await self._save_settings(llm=models.llm, vlm=models.vlm)

    async def save_default_limit(self, daily_limit: int) -> None:
        await self._save_settings(daily_limit=daily_limit)

    async def _save_settings(self, **values) -> None:
        async with self.database.begin() as conn:
            await conn.execute(insert(platform_settings).values(id=1, **values).on_conflict_do_update(
                index_elements=[platform_settings.c.id], set_=values,
            ))

    async def save_account_limit(self, account_id: UUID, daily_limit: int | None) -> bool:
        async with self.database.begin() as conn:
            if not await self._lock_account(conn, account_id):
                return False
            if daily_limit is None:
                await conn.execute(delete(account_quotas).where(account_quotas.c.account_id == account_id))
            else:
                await conn.execute(insert(account_quotas).values(account_id=account_id, daily_limit=daily_limit)
                    .on_conflict_do_update(index_elements=[account_quotas.c.account_id], set_={'daily_limit': daily_limit}))
            return True

    @staticmethod
    async def _lock_account(conn: AsyncConnection, account_id: UUID) -> bool:
        return (await conn.execute(select(accounts.c.id).where(accounts.c.id == account_id)
            .with_for_update())).scalar_one_or_none() is not None

    @staticmethod
    def _default_limit(row) -> int:
        return row['daily_limit'] if row is not None and row['daily_limit'] is not None else DEFAULT_DAILY_LIMIT

    async def admit_run(self, conn: AsyncConnection, account_id: UUID) -> tuple[PlatformModels, datetime]:
        # Hold this lock until the run is inserted so parallel sessions share one allowance.
        await self._lock_account(conn, account_id)
        at = datetime.now(timezone.utc)
        settings = (await conn.execute(_settings)).mappings().one_or_none()
        override = (await conn.execute(select(account_quotas.c.daily_limit).where(
            account_quotas.c.account_id == account_id,
        ))).scalar_one_or_none()
        limit = override if override is not None else self._default_limit(settings)
        statement = _usage(at).where(chat_sessions.c.account_id == account_id).with_only_columns(func.count(chat_runs.c.id))
        used = (await conn.execute(statement)).scalar_one_or_none() or 0
        if used >= limit:
            raise DailyLimitReached(used, limit)
        return self._models(settings), at

    async def quotas(self) -> dict:
        today = _usage(datetime.now(timezone.utc)).subquery()
        default = func.coalesce(_settings.with_only_columns(platform_settings.c.daily_limit).scalar_subquery(), DEFAULT_DAILY_LIMIT)
        daily_limit = func.coalesce(account_quotas.c.daily_limit, default)
        async with self.database.connect() as conn:
            rows = (await conn.execute(select(
                accounts.c.id, accounts.c.username, account_quotas.c.daily_limit, default.label('default_limit'),
                daily_limit.label('effective_limit'), func.coalesce(today.c.used, 0).label('used'),
            ).select_from(_account_quotas.outerjoin(today, today.c.account_id == accounts.c.id))
                .order_by(accounts.c.username))).mappings().all()
            return {'daily_limit': rows[0]['default_limit'] if rows else DEFAULT_DAILY_LIMIT, 'accounts': [dict(row) for row in rows]}
