from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.accounts import Operator
from api.dependencies import Runtime, require_write_origin
from connection.platform import PlatformModels


router = APIRouter(prefix='/api/ops', dependencies=[Depends(require_write_origin)])
DailyLimit = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]


class ChangeModels(BaseModel):
    llm: str
    vlm: str


class ChangeDefaultLimit(BaseModel):
    daily_limit: DailyLimit


class ChangeAccountLimit(BaseModel):
    daily_limit: DailyLimit | None


class AccountQuota(BaseModel):
    id: UUID
    username: str
    daily_limit: int | None
    used: int
    effective_limit: int


class Quotas(BaseModel):
    daily_limit: int
    accounts: list[AccountQuota]


@router.get('/models')
async def get_models(_operator: Operator, app: Runtime):
    models = await app.platform.models()
    return {**asdict(models), 'llms': app.platform.llms, 'vlms': app.platform.vlms}


@router.patch('/models')
async def set_models(body: ChangeModels, _operator: Operator, app: Runtime):
    if body.llm not in app.platform.llms or body.vlm not in app.platform.vlms:
        raise HTTPException(422, '请从已配置的文本模型和视觉模型中选择。')
    models = PlatformModels(body.llm, body.vlm)
    await app.platform.save_models(models)
    return {**asdict(models), 'llms': app.platform.llms, 'vlms': app.platform.vlms}


@router.get('/quotas', response_model=Quotas)
async def get_quotas(_operator: Operator, app: Runtime):
    return await app.platform.quotas()


@router.patch('/quotas', response_model=Quotas)
async def set_daily_limit(body: ChangeDefaultLimit, _operator: Operator, app: Runtime):
    await app.platform.save_default_limit(body.daily_limit)
    return await app.platform.quotas()


@router.patch('/quotas/{account_id}', response_model=Quotas)
async def set_account_limit(account_id: UUID, body: ChangeAccountLimit, _operator: Operator, app: Runtime):
    if not await app.platform.save_account_limit(account_id, body.daily_limit):
        raise HTTPException(404, '账号不存在。')
    return await app.platform.quotas()
