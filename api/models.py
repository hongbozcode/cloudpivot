from dataclasses import asdict, replace
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.concurrency import run_in_threadpool

from api.accounts import CurrentAccount
from api.chat import SESSION_MISSING
from api.dependencies import Runtime, require_write_origin
from connection.models import (
    ModelChoices, ModelKind, PersonalModel, RunModels, lock_account, model_choices, owned_profiles,
    resolve_models, save_choices, save_profile,
)
from connection.public_models import validate_base_url
from connection.schema import chat_sessions, personal_models


router = APIRouter(prefix='/api', dependencies=[Depends(require_write_origin)])
ModelText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r'^[^\x00-\x1f\x7f]+$')]
MODEL_MISSING = '模型配置不存在或无权访问。'


class ProfileSettings(BaseModel):
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
    model_id: ModelText
    input_budget: Annotated[int, Field(strict=True, ge=4096, le=2_000_000)] | None = None
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=200_000)] | None = None
    supports_temperature: bool = False
    temperature: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)] | None = None

    @model_validator(mode='after')
    def temperature_supported(self):
        if not self.supports_temperature and self.temperature is not None:
            raise ValueError('请仅为支持温度的模型配置温度。')
        return self


class ProfileUpdate(ProfileSettings):
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=2048)


class ProfileCreate(ProfileUpdate):
    kind: ModelKind
    api_key: SecretStr = Field(min_length=1, max_length=2048)


class Profile(ProfileSettings):
    id: UUID
    kind: ModelKind
    has_api_key: bool


class ChoiceUpdate(BaseModel):
    llm: UUID | None = None
    vlm: UUID | None = None

    def apply(self, current: ModelChoices) -> ModelChoices:
        return replace(current, **self.model_dump(exclude_unset=True))


def public_profile(row) -> Profile:
    return Profile(id=row.id, kind=row.kind, has_api_key=bool(row.api_key), **row.configuration)


async def profile_values(body: ProfileUpdate) -> tuple[dict, str | None]:
    key = body.api_key.get_secret_value().strip() if body.api_key is not None else None
    if key is not None and (not key or not key.isascii() or any(not c.isprintable() or c.isspace() for c in key)):
        raise HTTPException(422, 'API Key 格式无效。')
    try:
        base_url = await run_in_threadpool(validate_base_url, body.base_url)
    except (ValueError, httpx.InvalidURL) as exc:
        raise HTTPException(422, '请输入可解析到公网地址的 HTTPS Base URL。') from exc
    return {**body.model_dump(exclude={'api_key', 'kind'}), 'base_url': base_url}, key


@router.get('/models')
async def profiles(account: CurrentAccount, app: Runtime):
    async with app.platform.database.connect() as conn:
        rows = await conn.execute(owned_profiles(account.id).order_by(personal_models.c.id))
        return {
            'models': [public_profile(row) for row in rows],
            'defaults': await model_choices(conn, account.id),
            'platform': await app.platform.models(conn),
        }


@router.post('/models', response_model=Profile, status_code=201)
async def create_profile(body: ProfileCreate, account: CurrentAccount, app: Runtime):
    configuration, api_key = await profile_values(body)
    async with app.platform.database.begin() as conn:
        await lock_account(conn, account.id)
        return public_profile(await save_profile(conn, account.id, configuration, api_key, kind=body.kind))


@router.patch('/models/defaults')
async def set_defaults(body: ChoiceUpdate, account: CurrentAccount, app: Runtime):
    async with app.platform.database.begin() as conn:
        await lock_account(conn, account.id)
        choices = body.apply(await model_choices(conn, account.id))
        if not await save_choices(conn, account.id, choices, None):
            raise HTTPException(404, MODEL_MISSING)
    return choices


@router.patch('/models/{model_id}', response_model=Profile)
async def update_profile(model_id: UUID, body: ProfileUpdate, account: CurrentAccount, app: Runtime):
    async with app.platform.database.connect() as conn:
        if await conn.scalar(owned_profiles(account.id).with_only_columns(personal_models.c.id).where(
            personal_models.c.id == model_id,
        )) is None:
            raise HTTPException(404, MODEL_MISSING)
    configuration, api_key = await profile_values(body)
    async with app.platform.database.begin() as conn:
        await lock_account(conn, account.id)
        previous = (await conn.execute(owned_profiles(account.id).where(
            personal_models.c.id == model_id,
        ))).one()
        if api_key is None and previous.configuration['base_url'] != configuration['base_url']:
            raise HTTPException(422, '更换 Base URL 时，请填写该服务的 API Key。')
        return public_profile(await save_profile(conn, account.id, configuration, api_key, model_id=model_id))


async def session_settings(conn: AsyncConnection, account_id: UUID, session_id: UUID, app: Runtime) -> dict:
    platform = await app.platform.models(conn)
    models = await resolve_models(conn, account_id, session_id, RunModels(platform.llm, platform.vlm))
    effective = {}
    for kind in ('llm', 'vlm'):
        source, selected = models.select(kind)
        effective[kind] = {'source': source, 'model_id': selected.model_id if isinstance(selected, PersonalModel) else selected}
    return {'overrides': asdict(models.overrides), 'effective': effective}


@router.get('/sessions/{session_id}/models')
async def get_session_models(session_id: UUID, account: CurrentAccount, app: Runtime):
    if await app.chat.session(account.id, session_id) is None:
        raise HTTPException(404, SESSION_MISSING)
    async with app.platform.database.connect() as conn:
        return await session_settings(conn, account.id, session_id, app)


@router.patch('/sessions/{session_id}/models')
async def set_session_models(session_id: UUID, body: ChoiceUpdate, account: CurrentAccount, app: Runtime):
    async with app.platform.database.begin() as conn:
        row = (await conn.execute(select(chat_sessions.c.archived_at).where(
            chat_sessions.c.id == session_id, chat_sessions.c.account_id == account.id,
        ).with_for_update())).one_or_none()
        if row is None:
            raise HTTPException(404, SESSION_MISSING)
        if row.archived_at is not None:
            raise HTTPException(409, '已归档的会话无法修改模型。')
        await lock_account(conn, account.id)
        choices = body.apply(await model_choices(conn, account.id, session_id))
        if not await save_choices(conn, account.id, choices, session_id):
            raise HTTPException(404, MODEL_MISSING)
        return await session_settings(conn, account.id, session_id, app)
