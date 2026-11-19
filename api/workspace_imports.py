import asyncio
import shutil
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import String, cast, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from api.accounts import Operator
from api.dependencies import Runtime, require_write_origin
from connection.models import lock_account
from connection.schema import accounts, chat_sessions, session_files, workspace_import_receipts
from content.legacy_workspace import (
    InvalidWorkspace, WorkspaceChanged, copy_workspace, legacy_source, regular_directory, workspace_manifest,
)
from content.run_artifacts import ARTIFACT_NAMESPACE
from content.session_files import ORIGINALS_NAMESPACE
from content.workspace import WORKSPACE_NAMESPACE


router = APIRouter(prefix='/api/ops/workspace-imports', dependencies=[Depends(require_write_origin)])


class ImportSource(BaseModel):
    source_type: Literal['session', 'directory']
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024, pattern=r'^[^\x00]+$')]
    account_id: UUID


class ConfirmImport(ImportSource):
    confirmation: str = Field(pattern=r'^[0-9a-f]{64}$')


async def review(conn: AsyncConnection, body: ImportSource, base: Path, workspace: Path):
    from core.config import CONVERSATION_HISTORY_PATH

    username = (await conn.execute(select(accounts.c.username).where(
        accounts.c.id == body.account_id,
    ))).scalar_one_or_none()
    if username is None:
        raise HTTPException(404, '目标账号不存在。')
    try:
        source = await asyncio.to_thread(legacy_source, base, body.source_type, body.source)
        await asyncio.to_thread(regular_directory, source, base)
        source = await asyncio.to_thread(source.resolve, strict=True)
        if not source.is_relative_to(base):
            raise InvalidWorkspace('旧工作区目录必须位于配置的旧工作区根目录内。')
        excluded_directories = {CONVERSATION_HISTORY_PATH, 'builtin-skills', '.git'}
        if any(part.casefold() in excluded_directories for part in source.relative_to(base).parts):
            raise InvalidWorkspace('请选择包含旧会话文件或 Skill 的工作区目录。')
        if source.is_relative_to(workspace):
            parts = source.relative_to(workspace).parts
            if parts and parts[0].casefold() in {ORIGINALS_NAMESPACE, ARTIFACT_NAMESPACE}:
                raise InvalidWorkspace('请选择旧工作区目录。')
            if parts and parts[0].casefold() == WORKSPACE_NAMESPACE:
                if len(parts) < 2:
                    raise InvalidWorkspace('请选择具体的旧会话目录。')
                active = await conn.scalar(select(chat_sessions.c.id).where(
                    func.encode(func.sha256(func.convert_to(cast(chat_sessions.c.id, String), 'UTF8')), 'hex')
                    == parts[1].lower(),
                ).limit(1))
                if active is not None:
                    raise InvalidWorkspace('请选择旧工作区，当前站点的会话不能迁入。')
        manifest = await asyncio.to_thread(workspace_manifest, source)
    except InvalidWorkspace as exc:
        raise HTTPException(404, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(422, '无法读取旧工作区，请检查目录和文件权限。') from exc
    return username, manifest


def roots(app: Runtime) -> tuple[Path, Path]:
    from core import config

    workspace = Path(config.ROOT_PATH_AGENT).resolve()
    base = Path(app.settings.legacy_workspace_root).resolve() if app.settings.legacy_workspace_root else workspace
    return base, workspace


@router.get('/accounts')
async def targets(_operator: Operator, app: Runtime):
    async with app.files.database.connect() as conn:
        rows = await conn.execute(select(accounts.c.id, accounts.c.username).order_by(accounts.c.username))
        return [dict(row) for row in rows.mappings()]


@router.post('/preview')
async def preview(body: ImportSource, _operator: Operator, app: Runtime):
    async with app.files.database.connect() as conn:
        username, manifest = await review(conn, body, *roots(app))
    return {
        'source': body.source if body.source_type == 'session' else str(manifest.root),
        'username': username, 'files': manifest.files,
        'skills': manifest.skills, 'skill_files': manifest.skill_files,
        'confirmation': manifest.confirmation(body.account_id),
    }


@router.post('', status_code=201)
async def apply_import(body: ConfirmImport, _operator: Operator, app: Runtime):
    session_id = uuid4()
    created: list[Path] = []
    try:
        async with app.files.database.begin() as conn:
            await lock_account(conn, body.account_id)
            existing = await conn.scalar(select(workspace_import_receipts.c.session_id).join(chat_sessions).where(
                workspace_import_receipts.c.confirmation == body.confirmation, chat_sessions.c.account_id == body.account_id,
            ))
            if existing is not None:
                return {'session_id': existing}
            _, manifest = await review(conn, body, *roots(app))
            if manifest.confirmation(body.account_id) != body.confirmation:
                raise WorkspaceChanged
            copying = asyncio.create_task(asyncio.to_thread(copy_workspace, manifest, session_id, created))
            try:
                files = await asyncio.shield(copying)
            except asyncio.CancelledError:
                await asyncio.gather(copying, return_exceptions=True)
                raise
            await conn.execute(insert(chat_sessions).values(
                id=session_id, account_id=body.account_id, title=f'迁入 · {body.source}'[:100],
            ))
            if files:
                await conn.execute(insert(session_files), [
                    {'id': file.id, 'session_id': session_id, 'name': file.name, 'workspace_key': file.key}
                    for file in files
                ])
            await conn.execute(insert(workspace_import_receipts).values(
                confirmation=body.confirmation, session_id=session_id,
            ))
    except BaseException as exc:
        for path in reversed(created):
            # Only paths created by copy_workspace are eligible for rollback.
            if path.resolve() == path and not path.is_symlink():
                await asyncio.to_thread(shutil.rmtree, path)
        if isinstance(exc, (WorkspaceChanged, InvalidWorkspace)):
            raise HTTPException(409, '旧工作区内容已变化，请重新核对迁入摘要。') from exc
        if isinstance(exc, OSError):
            raise HTTPException(422, '无法复制旧工作区，请检查文件权限和存储空间。') from exc
        raise
    return {'session_id': session_id}
