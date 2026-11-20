import asyncio
import logging
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

from api.accounts import CurrentAccount
from api.chat import SESSION_MISSING
from api.dependencies import Runtime, require_write_origin
from connection.files import ReadOnlyWorkspace
from content.file_preview import PdfPreview, make_preview
from content.session_files import valid_file_name


router = APIRouter(prefix='/api/sessions/{session_id}/files', dependencies=[Depends(require_write_origin)])
FILE_MISSING = '文件不存在或无权访问。'
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FILE_HEADERS = {'X-Content-Type-Options': 'nosniff'}
PDF_PAGES_HEADER = 'X-CloudPivot-PDF-Pages'
logger = logging.getLogger(__name__)


class SessionFile(BaseModel):
    id: UUID
    name: str
    kind: Literal['source', 'artifact']
    revision: str | None = None


async def require_session(session_id: UUID, account: CurrentAccount, app: Runtime) -> None:
    if await app.chat.session(account.id, session_id) is None:
        raise HTTPException(404, SESSION_MISSING)


@router.get('', response_model=list[SessionFile], dependencies=[Depends(require_session)])
async def list_files(session_id: UUID, app: Runtime):
    return await app.files.list(session_id)


@router.post('', status_code=201, response_model=SessionFile, dependencies=[Depends(require_session)])
async def upload_file(
    session_id: UUID, account: CurrentAccount, app: Runtime, request: Request,
    name: Annotated[str, Query(min_length=1, max_length=200)],
):
    if not valid_file_name(name):
        raise HTTPException(422, '文件名无效或过长，请重命名后重试。')
    try:
        size = 0
        with SpooledTemporaryFile(max_size=1024 * 1024) as source:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, '单个附件不能超过 25 MB。')
                await asyncio.to_thread(source.write, chunk)
            file = await app.files.upload(account.id, session_id, name, source)
    except ReadOnlyWorkspace as exc:
        raise HTTPException(409, '运行中或已归档的会话无法上传文件。') from exc
    except (ClientDisconnect, OSError, ValueError) as exc:
        raise HTTPException(400, '文件上传失败，请重试。') from exc
    if file is None:
        raise HTTPException(404, SESSION_MISSING)
    return file


async def resolve_file(
    session_id: UUID, file_id: UUID, app: Runtime, _owner: Annotated[None, Depends(require_session)],
) -> tuple[Path, str]:
    file = await app.files.get(session_id, file_id)
    if file is not None:
        try:
            path = file.path(session_id)
            if path.is_file():
                return path, file.name
        except (OSError, ValueError):
            pass
    raise HTTPException(404, FILE_MISSING)


ResolvedFile = Annotated[tuple[Path, str], Depends(resolve_file)]


@router.get('/{file_id}/download', response_class=FileResponse)
async def download_file(file: ResolvedFile) -> FileResponse:
    path, name = file
    return FileResponse(path, filename=name, media_type='application/octet-stream', headers=FILE_HEADERS)


@router.get('/{file_id}/preview')
async def preview_file(
    file: ResolvedFile, sheet: Annotated[int, Query(ge=0)] = 0, page: Annotated[int, Query(ge=0)] = 0,
):
    path, name = file
    return await render_file_preview(path, name, sheet, page)


async def render_file_preview(path: Path, name: str, sheet: int, page: int, *, binary: bytes | None = None):
    try:
        preview = await asyncio.to_thread(make_preview, path, Path(name).suffix.lower(), sheet, page)
    except Exception as exc:
        logger.info('File preview failed (%s)', type(exc).__name__)
        raise HTTPException(422, '文件预览失败，文件可能损坏、过大或格式不受支持。') from exc
    if preview is None:
        raise HTTPException(415, '此文件格式可下载查看。')
    if isinstance(preview, PdfPreview):
        return Response(preview.png, media_type='image/png', headers={**FILE_HEADERS, PDF_PAGES_HEADER: str(preview.page_count)})
    if isinstance(preview, str):
        # The workbench fetches bytes. Opening this URL directly must also be safe.
        headers = {**FILE_HEADERS, 'Content-Security-Policy': "sandbox; default-src 'none'"}
        if binary is not None:
            return Response(binary, media_type=preview, headers=headers)
        return FileResponse(path, filename=name, media_type=preview, headers=headers)
    return preview
