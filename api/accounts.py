import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from api.dependencies import Runtime, require_write_origin
from connection.accounts import UsernameTaken
from core.security import hash_password, new_token, token_digest, verify_password


COOKIE = 'cloudpivot_session'
ACCOUNT_PATTERN = re.compile(r'^[a-z0-9_][a-z0-9_.-]{2,63}$')
INVALID_INVITATION = '邀请无效、已使用或已过期。请向运营人员获取新链接。'
router = APIRouter(prefix='/api', dependencies=[Depends(require_write_origin)])


class Account(BaseModel):
    id: UUID
    username: str
    role: Literal['member', 'operator']


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256, repr=False)

    @field_validator('username')
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class InvitationToken(BaseModel):
    token: str = Field(min_length=1, max_length=128, repr=False)


class Registration(Credentials, InvitationToken):
    password: str = Field(min_length=10, max_length=256, repr=False)

    @field_validator('username')
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not ACCOUNT_PATTERN.fullmatch(value):
            raise ValueError('账号格式无效。')
        return value


def request_digest(request: Request) -> bytes | None:
    token = request.cookies.get(COOKIE, '')
    return token_digest(token) if len(token) == 43 else None


async def require_account(request: Request, app: Runtime) -> Account:
    digest = request_digest(request)
    row = await app.accounts.account_for_session(digest) if digest else None
    if row is None:
        raise HTTPException(401, '登录已失效，请重新登录。')
    return Account.model_validate(row)


CurrentAccount = Annotated[Account, Depends(require_account)]


async def require_operator(account: CurrentAccount) -> Account:
    if account.role != 'operator':
        raise HTTPException(403, '需要运营权限。')
    return account


Operator = Annotated[Account, Depends(require_operator)]


@router.get('/auth/me')
async def me(account: CurrentAccount) -> Account:
    return account


@router.post('/auth/login')
async def login(body: Credentials, request: Request, response: Response, app: Runtime) -> Account:
    row = await app.accounts.login_account(body.username)
    encoded = row['password_hash'] if row else None
    if not await run_in_threadpool(verify_password, encoded, body.password) or row is None:
        raise HTTPException(401, '账号或密码错误。')

    token = new_token()
    await app.accounts.start_session(
        row['id'], token_digest(token), app.settings.session_seconds, request_digest(request),
    )
    response.set_cookie(
        COOKIE, token, max_age=app.settings.session_seconds, path='/api',
        httponly=True, secure=app.settings.cookie_secure, samesite='lax',
    )
    return Account.model_validate(row)


@router.post('/auth/logout', status_code=204)
async def logout(request: Request, response: Response, app: Runtime) -> None:
    if digest := request_digest(request):
        await app.accounts.end_session(digest)
    response.delete_cookie(COOKIE, path='/api', httponly=True, secure=app.settings.cookie_secure)


@router.post('/auth/invitation')
async def check_invitation(body: InvitationToken, app: Runtime) -> dict:
    row = await app.accounts.pending_invitation(token_digest(body.token))
    if row is None:
        raise HTTPException(400, INVALID_INVITATION)
    return row


@router.post('/auth/register', status_code=201)
async def register(body: Registration, app: Runtime) -> Account:
    digest = token_digest(body.token)
    if await app.accounts.pending_invitation(digest) is None:
        raise HTTPException(400, INVALID_INVITATION)
    encoded = await run_in_threadpool(hash_password, body.password)
    try:
        row = await app.accounts.register(digest, body.username, encoded)
    except UsernameTaken as exc:
        raise HTTPException(409, '这个账号已被使用，请换一个账号。') from exc
    if row is None:
        raise HTTPException(400, INVALID_INVITATION)
    return Account.model_validate(row)


@router.get('/ops/invitations')
async def list_invitations(_operator: Operator, app: Runtime) -> list[dict]:
    return await app.accounts.invitations()


@router.post('/ops/invitations', status_code=201)
async def create_invitation(operator: Operator, app: Runtime) -> dict:
    token = new_token()
    return {**await app.accounts.add_invitation(operator.id, token_digest(token)), 'token': token}
