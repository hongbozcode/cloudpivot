from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Identity, Index,
    Integer, LargeBinary, MetaData, Table, Text, UniqueConstraint, Uuid, func, text,
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
platform_settings = Table(
    'platform_settings', metadata,
    Column('id', Integer, primary_key=True),
    Column('llm', Text),
    Column('vlm', Text),
    Column('daily_limit', Integer),
    CheckConstraint('id = 1 AND daily_limit >= 0', name='platform_settings_values'),
)
account_quotas = Table(
    'account_quotas', metadata,
    Column('account_id', Uuid, ForeignKey('accounts.id'), primary_key=True),
    Column('daily_limit', Integer, nullable=False),
    CheckConstraint('daily_limit >= 0', name='account_daily_limit'),
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
personal_models = Table(
    'personal_models', metadata,
    Column('id', Uuid, primary_key=True),
    Column('account_id', Uuid, ForeignKey('accounts.id'), nullable=False, index=True),
    Column('kind', Text, nullable=False),
    Column('api_key', Text, nullable=False),
    Column('configuration', JSONB, nullable=False),
    CheckConstraint("kind IN ('llm', 'vlm')", name='personal_model_kind'),
)
account_model_defaults = Table(
    'account_model_defaults', metadata,
    Column('account_id', Uuid, ForeignKey('accounts.id'), primary_key=True),
    Column('llm', Uuid, ForeignKey('personal_models.id')),
    Column('vlm', Uuid, ForeignKey('personal_models.id')),
)
session_model_overrides = Table(
    'session_model_overrides', metadata,
    Column('session_id', Uuid, ForeignKey('chat_sessions.id'), primary_key=True),
    Column('llm', Uuid, ForeignKey('personal_models.id')),
    Column('vlm', Uuid, ForeignKey('personal_models.id')),
)
mcp_connections = Table(
    'mcp_connections', metadata,
    Column('id', Uuid, primary_key=True),
    Column('account_id', Uuid, ForeignKey('accounts.id'), nullable=False, index=True),
    Column('name', Text, nullable=False),
    Column('url', Text, nullable=False),
    Column('catalog_id', Uuid),
    Column('tools', JSONB, nullable=False, server_default=text("'[]'::jsonb")),
)
workspace_import_receipts = Table(
    'workspace_import_receipts', metadata,
    Column('confirmation', Text, primary_key=True),
    Column('session_id', Uuid, ForeignKey('chat_sessions.id'), nullable=False),
)
chat_runs = Table(
    'chat_runs', metadata,
    Column('id', Uuid, primary_key=True),
    Column('session_id', Uuid, ForeignKey('chat_sessions.id'), nullable=False),
    Column('status', Text, nullable=False, server_default='running'),
    Column('started_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('finished_at', DateTime(timezone=True)),
    Column('uses_platform_model', Boolean, nullable=False, server_default=text('true')),
    CheckConstraint("status IN ('running', 'completed', 'failed', 'interrupted', 'stopped')", name='chat_run_status'),
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
tool_approvals = Table(
    'tool_approvals', metadata,
    Column('id', Uuid, primary_key=True),
    Column('run_id', Uuid, ForeignKey('chat_runs.id'), nullable=False, index=True),
    Column('status', Text, nullable=False, server_default='pending'),
    CheckConstraint("status IN ('pending', 'approved', 'rejected', 'cancelled')", name='tool_approval_status'),
)
session_files = Table(
    'session_files', metadata,
    Column('id', Uuid, primary_key=True),
    Column('session_id', Uuid, ForeignKey('chat_sessions.id'), nullable=False, index=True),
    Column('name', Text, nullable=False),
    Column('workspace_key', Text),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
)
run_attachments = Table(
    'run_attachments', metadata,
    Column('run_id', Uuid, ForeignKey('chat_runs.id'), primary_key=True),
    Column('position', Integer, primary_key=True),
    Column('file_id', Uuid, ForeignKey('session_files.id'), nullable=False),
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
