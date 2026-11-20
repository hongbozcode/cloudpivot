import asyncio
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from api.accounts import CurrentAccount
from api.chat import SESSION_MISSING
from api.dependencies import Runtime, require_write_origin
from api.files import FILE_HEADERS, render_file_preview, require_session
from connection.skills import SkillNameConflict, SkillPublishBusy, SkillRevisionChanged
from content.skills_package import (
    MAX_PACKAGE_BYTES, SKILL_FILENAME, SkillBundle, SkillFormatError, list_session_skills, read_session_skill, unpack_bundle,
)


router = APIRouter(prefix='/api/skills', dependencies=[Depends(require_write_origin)])
session_router = APIRouter(
    prefix='/api/sessions/{session_id}/skills', dependencies=[Depends(require_write_origin), Depends(require_session)],
)
SKILL_MISSING = 'Skill 不存在或无权访问。'
NAME_CONFLICT = '个人技能库已有同名 Skill。请修改 name 后重试。'


class SkillSummary(BaseModel):
    name: str
    description: str


class PersonalSkill(SkillSummary):
    id: UUID
    enabled: bool


class SkillAsset(BaseModel):
    path: str
    size: int


class SkillPreview(SkillSummary):
    markdown: str
    assets: list[SkillAsset]


class PersonalSkillPreview(SkillPreview, PersonalSkill):
    pass


class SessionSkillPreview(SkillPreview):
    revision: str
    published: bool


class SkillPublication(BaseModel):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')


class SkillSettings(BaseModel):
    enabled: bool


def _preview(files: dict[str, bytes]) -> dict:
    return {
        'markdown': files[SKILL_FILENAME].decode('utf-8'),
        'assets': [{'path': path, 'size': len(data)} for path, data in sorted(files.items()) if path != SKILL_FILENAME],
    }


async def _asset(files: dict[str, bytes], path: str, download: bool, sheet: int, page: int):
    if path == SKILL_FILENAME or path not in files:
        raise HTTPException(404, '配套文件不存在或无权访问。')
    name = PurePosixPath(path).name
    data = files[path]
    if download:
        return Response(data, media_type='application/octet-stream', headers={
            **FILE_HEADERS, 'Content-Disposition': f"attachment; filename*=UTF-8''{quote(name, safe='')}",
        })
    with TemporaryDirectory(prefix='cloudpivot-preview-') as directory:
        temporary = Path(directory) / 'asset'
        await asyncio.to_thread(temporary.write_bytes, data)
        return await render_file_preview(temporary, name, sheet, page, binary=data)


async def _personal_skill(account: CurrentAccount, app: Runtime, skill_id: UUID) -> dict:
    skill = await app.skills.get(account.id, skill_id)
    if skill is None:
        raise HTTPException(404, SKILL_MISSING)
    return skill


async def _session_skill(session_id: UUID, name: str) -> SkillBundle:
    try:
        return await asyncio.to_thread(read_session_skill, session_id, name)
    except SkillFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(404, SKILL_MISSING) from exc


@router.get('', response_model=list[PersonalSkill])
async def list_personal_skills(account: CurrentAccount, app: Runtime):
    return await app.skills.list(account.id)


@router.post('', status_code=201, response_model=PersonalSkill)
async def import_personal_skill(
    account: CurrentAccount, app: Runtime, request: Request, name: Annotated[str, Query(min_length=1, max_length=200)],
):
    data = bytearray()
    try:
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_PACKAGE_BYTES:
                raise HTTPException(413, 'Skill 导入文件不能超过 25 MB。')
            data.extend(chunk)
        bundle = await asyncio.to_thread(SkillBundle.upload, name, bytes(data))
        return await app.skills.add(account.id, bundle)
    except ClientDisconnect as exc:
        raise HTTPException(400, 'Skill 上传中断，请重试。') from exc
    except SkillFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SkillNameConflict as exc:
        raise HTTPException(409, NAME_CONFLICT) from exc


@router.get('/{skill_id}', response_model=PersonalSkillPreview)
async def preview_personal_skill(skill: Annotated[dict, Depends(_personal_skill)]):
    files = await asyncio.to_thread(unpack_bundle, skill.pop('bundle'))
    return {**skill, **_preview(files)}


@router.patch('/{skill_id}', response_model=PersonalSkill)
async def update_personal_skill(account: CurrentAccount, app: Runtime, skill_id: UUID, settings: SkillSettings):
    updated = await app.skills.set_enabled(account.id, skill_id, settings.enabled)
    if updated is None:
        raise HTTPException(404, SKILL_MISSING)
    return updated


@router.get('/{skill_id}/asset')
async def personal_skill_asset(
    skill: Annotated[dict, Depends(_personal_skill)], path: Annotated[str, Query(min_length=1, max_length=512)],
    download: bool = False, sheet: Annotated[int, Query(ge=0)] = 0, page: Annotated[int, Query(ge=0)] = 0,
):
    files = await asyncio.to_thread(unpack_bundle, skill['bundle'])
    return await _asset(files, path, download, sheet, page)


@session_router.get('', response_model=list[SkillSummary])
async def session_skills(session_id: UUID):
    try:
        return await asyncio.to_thread(list_session_skills, session_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, '会话技能目录无法读取。') from exc


@session_router.get('/{name}', response_model=SessionSkillPreview)
async def preview_session_skill(account: CurrentAccount, app: Runtime, skill: Annotated[SkillBundle, Depends(_session_skill)]):
    revision = await asyncio.to_thread(skill.revision)
    return {
        'name': skill.definition.name, 'description': skill.definition.description, **_preview(skill.files),
        'revision': revision, 'published': await app.skills.has_revision(account.id, skill.definition.name, revision),
    }


@session_router.get('/{name}/asset')
async def session_skill_asset(
    skill: Annotated[SkillBundle, Depends(_session_skill)], path: Annotated[str, Query(min_length=1, max_length=512)],
    download: bool = False, sheet: Annotated[int, Query(ge=0)] = 0, page: Annotated[int, Query(ge=0)] = 0,
):
    return await _asset(skill.files, path, download, sheet, page)


@session_router.post('/{name}/publish', status_code=201, response_model=PersonalSkill)
async def publish_session_skill(account: CurrentAccount, app: Runtime, session_id: UUID, name: str, body: SkillPublication):
    try:
        saved = await app.skills.publish(account.id, session_id, name, body.revision)
    except SkillNameConflict as exc:
        raise HTTPException(409, NAME_CONFLICT) from exc
    except SkillPublishBusy as exc:
        raise HTTPException(409, '请在本次运行结束后再加入个人技能库。') from exc
    except SkillRevisionChanged as exc:
        raise HTTPException(409, '会话 Skill 已更新。请重新查看预览后再加入个人技能库。') from exc
    except SkillFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(404, SKILL_MISSING) from exc
    if saved is None:
        raise HTTPException(404, SESSION_MISSING)
    return saved
