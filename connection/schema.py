from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Identity, Index,
    LargeBinary, MetaData, Table, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB


metadata = MetaData()
accounts = Table(
    'accounts', metadata,
    Column('id', Uuid, primary_key=True),
    Column('username', Text, nullable=False, unique=True),
    Column('password_hash', Text, nullable=False),
    Column('role', Text, nullable=False),
    CheckConstraint("role IN ('member', 'operator')", name='account_role'),
)
invitations = Table(
    'invitations', metadata,
    Column('id', BigInteger, Identity(always=True), primary_key=True),
    Column('token_digest', LargeBinary, nullable=False, unique=True),
    Column('created_by', Uuid, ForeignKey('accounts.id'), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('expires_at', DateTime(timezone=True), nullable=False, server_default=text("now() + interval '7 days'")),
    Column('registered_by', Uuid, ForeignKey('accounts.id')),
    Column('registered_at', DateTime(timezone=True)),
)
account_sessions = Table(
    'account_sessions', metadata,
    Column('token_digest', LargeBinary, primary_key=True),
    Column('account_id', Uuid, ForeignKey('accounts.id'), nullable=False),
    Column('expires_at', DateTime(timezone=True), nullable=False, index=True),
)
chat_sessions = Table(
    'chat_sessions', metadata,
    Column('id', Uuid, primary_key=True),
    Column('account_id', Uuid, ForeignKey('accounts.id'), nullable=False),
    Column('title', Text, nullable=False, server_default='新会话'),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('updated_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('archived_at', DateTime(timezone=True)),
)
chat_runs = Table(
    'chat_runs', metadata,
    Column('id', Uuid, primary_key=True),
    Column('session_id', Uuid, ForeignKey('chat_sessions.id'), nullable=False),
    Column('status', Text, nullable=False, server_default='running'),
    Column('started_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('finished_at', DateTime(timezone=True)),
    CheckConstraint("status IN ('running', 'completed', 'failed', 'interrupted')", name='chat_run_status'),
)
chat_messages = Table(
    'chat_messages', metadata,
    Column('id', BigInteger, Identity(always=True), primary_key=True),
    Column('session_id', Uuid, ForeignKey('chat_sessions.id'), nullable=False),
    Column('run_id', Uuid, ForeignKey('chat_runs.id'), nullable=False),
    Column('role', Text, nullable=False),
    Column('text', Text, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("role IN ('user', 'assistant')", name='chat_message_role'),
    UniqueConstraint('run_id', 'role'),
)
chat_run_events = Table(
    'chat_run_events', metadata,
    Column('id', BigInteger, Identity(always=True), primary_key=True),
    Column('run_id', Uuid, ForeignKey('chat_runs.id'), nullable=False),
    Column('data', JSONB, nullable=False),
)

Index(
    'chat_sessions_account', chat_sessions.c.account_id, chat_sessions.c.updated_at.desc(),
    postgresql_where=chat_sessions.c.archived_at.is_(None),
)
Index(
    'chat_runs_one_active', chat_runs.c.session_id, unique=True,
    postgresql_where=chat_runs.c.status == 'running',
)
Index('chat_runs_session', chat_runs.c.session_id, chat_runs.c.started_at.desc())
Index('chat_messages_session', chat_messages.c.session_id, chat_messages.c.id)
Index('chat_run_events_run', chat_run_events.c.run_id, chat_run_events.c.id)
