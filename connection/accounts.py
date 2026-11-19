from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from connection.schema import account_sessions, accounts, invitations


class UsernameTaken(Exception):
    pass


class AccountStore:
    def __init__(self, database: AsyncEngine):
        self.database = database

    async def login_account(self, username: str) -> dict | None:
        async with self.database.connect() as conn:
            row = (await conn.execute(select(accounts).where(accounts.c.username == username))).mappings().one_or_none()
            return dict(row) if row else None

    async def account_for_session(self, digest: bytes) -> dict | None:
        async with self.database.connect() as conn:
            row = (await conn.execute(
                select(accounts.c.id, accounts.c.username, accounts.c.role)
                .select_from(account_sessions.join(accounts))
                .where(account_sessions.c.token_digest == digest, account_sessions.c.expires_at > func.now())
            )).mappings().one_or_none()
            return dict(row) if row else None

    async def start_session(
        self, account_id: UUID, digest: bytes, expires_in: int, previous_digest: bytes | None,
    ) -> None:
        async with self.database.begin() as conn:
            await conn.execute(delete(account_sessions).where(or_(
                account_sessions.c.expires_at <= func.now(),
                account_sessions.c.token_digest == previous_digest,
            )))
            await conn.execute(insert(account_sessions).values(
                token_digest=digest, account_id=account_id,
                expires_at=func.now() + timedelta(seconds=expires_in),
            ))

    async def end_session(self, digest: bytes) -> None:
        async with self.database.begin() as conn:
            await conn.execute(delete(account_sessions).where(account_sessions.c.token_digest == digest))

    async def add_invitation(self, creator_id: UUID, digest: bytes) -> dict:
        async with self.database.begin() as conn:
            row = (await conn.execute(
                insert(invitations).values(created_by=creator_id, token_digest=digest).returning(
                    invitations.c.id, invitations.c.created_at, invitations.c.expires_at,
                )
            )).mappings().one()
            return {**row, 'status': 'pending'}

    async def invitations(self) -> list[dict]:
        status = case(
            (invitations.c.registered_by.is_not(None), 'registered'),
            (invitations.c.expires_at <= func.now(), 'expired'),
            else_='pending',
        )
        async with self.database.connect() as conn:
            result = await conn.execute(
                select(
                    invitations.c.id, invitations.c.created_at, invitations.c.expires_at,
                    invitations.c.registered_at, accounts.c.username.label('registered_username'),
                    status.label('status'),
                )
                .select_from(invitations.outerjoin(accounts, invitations.c.registered_by == accounts.c.id))
                .order_by(invitations.c.id.desc())
            )
            return [dict(row) for row in result.mappings()]

    @staticmethod
    def _pending(digest: bytes):
        return (
            invitations.c.token_digest == digest,
            invitations.c.registered_by.is_(None),
            invitations.c.expires_at > func.now(),
        )

    async def pending_invitation(self, digest: bytes) -> dict | None:
        async with self.database.connect() as conn:
            row = (await conn.execute(
                select(invitations.c.expires_at).where(*self._pending(digest))
            )).mappings().one_or_none()
            return dict(row) if row else None

    async def register(self, digest: bytes, username: str, password_hash: str) -> dict | None:
        async with self.database.begin() as conn:
            invitation_id = (await conn.execute(
                select(invitations.c.id).where(*self._pending(digest)).with_for_update()
            )).scalar_one_or_none()
            if invitation_id is None:
                return None
            account_id = uuid4()
            try:
                await conn.execute(insert(accounts).values(
                    id=account_id, username=username, password_hash=password_hash, role='member',
                ))
            except IntegrityError as exc:
                if getattr(exc.orig, 'sqlstate', '') == '23505':
                    raise UsernameTaken from exc
                raise
            await conn.execute(update(invitations).where(invitations.c.id == invitation_id).values(
                registered_by=account_id, registered_at=func.now(),
            ))
            return {'id': account_id, 'username': username, 'role': 'member'}

    async def create_operator(self, username: str, password_hash: str) -> None:
        try:
            async with self.database.begin() as conn:
                await conn.execute(insert(accounts).values(
                    id=uuid4(), username=username, password_hash=password_hash, role='operator',
                ))
        except IntegrityError as exc:
            if getattr(exc.orig, 'sqlstate', '') == '23505':
                raise UsernameTaken from exc
            raise
