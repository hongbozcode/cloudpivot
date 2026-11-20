import asyncio
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from connection.schema import chat_runs, chat_sessions, personal_skills
from content.skills_package import SkillBundle, read_session_skill


class SkillNameConflict(Exception):
    pass


class SkillPublishBusy(Exception):
    pass


class SkillRevisionChanged(Exception):
    pass


_columns = (personal_skills.c.id, personal_skills.c.name, personal_skills.c.description, personal_skills.c.enabled)


async def enabled_skill_packages(conn: AsyncConnection, account_id: UUID) -> tuple[tuple[str, bytes], ...]:
    result = await conn.execute(select(personal_skills.c.name, personal_skills.c.bundle).where(
        personal_skills.c.account_id == account_id, personal_skills.c.enabled.is_(True),
    ).order_by(personal_skills.c.name))
    return tuple((name, bundle) for name, bundle in result)


class SkillLibrary:
    def __init__(self, database: AsyncEngine):
        self.database = database

    async def list(self, account_id: UUID) -> list[dict]:
        async with self.database.connect() as conn:
            result = await conn.execute(select(*_columns).where(
                personal_skills.c.account_id == account_id,
            ).order_by(personal_skills.c.name))
            return [dict(row) for row in result.mappings()]

    async def get(self, account_id: UUID, skill_id: UUID) -> dict | None:
        async with self.database.connect() as conn:
            row = (await conn.execute(select(*_columns, personal_skills.c.bundle).where(
                personal_skills.c.account_id == account_id, personal_skills.c.id == skill_id,
            ))).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def set_enabled(self, account_id: UUID, skill_id: UUID, enabled: bool) -> dict | None:
        async with self.database.begin() as conn:
            row = (await conn.execute(update(personal_skills).where(
                personal_skills.c.account_id == account_id, personal_skills.c.id == skill_id,
            ).values(enabled=enabled).returning(*_columns))).mappings().one_or_none()
            return dict(row) if row is not None else None

    async def has_revision(self, account_id: UUID, name: str, revision: str) -> bool:
        async with self.database.connect() as conn:
            match = (await conn.execute(select(personal_skills.c.id).where(
                personal_skills.c.account_id == account_id, personal_skills.c.name == name,
                personal_skills.c.revision == revision,
            ))).scalar_one_or_none()
            return match is not None

    async def add(self, account_id: UUID, skill: SkillBundle) -> dict:
        revision = await asyncio.to_thread(skill.revision)
        async with self.database.begin() as conn:
            return await self._save(conn, account_id, skill, revision)

    async def publish(self, account_id: UUID, session_id: UUID, name: str, revision: str) -> dict | None:
        async with self.database.begin() as conn:
            owned = (await conn.execute(select(chat_sessions.c.id).where(
                chat_sessions.c.account_id == account_id, chat_sessions.c.id == session_id,
            ).with_for_update())).scalar_one_or_none()
            if owned is None:
                return None
            active = (await conn.execute(select(chat_runs.c.id).where(
                chat_runs.c.session_id == session_id, chat_runs.c.status == 'running',
            ))).scalar_one_or_none()
            if active is not None:
                raise SkillPublishBusy
            skill = await asyncio.to_thread(read_session_skill, session_id, name)
            if await asyncio.to_thread(skill.revision) != revision:
                raise SkillRevisionChanged
            return await self._save(conn, account_id, skill, revision, session_id)

    async def _save(
        self, conn: AsyncConnection, account_id: UUID, skill: SkillBundle, revision: str, session_id: UUID | None = None,
    ) -> dict:
        bundle = await asyncio.to_thread(skill.pack)
        statement = insert(personal_skills).values(
            id=uuid4(), account_id=account_id, name=skill.definition.name, description=skill.definition.description,
            bundle=bundle, revision=revision, source_session_id=session_id,
        )
        row = (await conn.execute(statement.on_conflict_do_update(
            index_elements=[personal_skills.c.account_id, personal_skills.c.name],
            set_={'bundle': bundle, 'description': skill.definition.description, 'revision': revision},
            where=or_(personal_skills.c.revision == revision, and_(
                personal_skills.c.source_session_id.is_not(None), personal_skills.c.source_session_id == session_id,
            )),
        ).returning(*_columns))).mappings().one_or_none()
        if row is None:
            raise SkillNameConflict
        return dict(row)
