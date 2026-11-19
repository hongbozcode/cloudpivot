from typing import Annotated

from fastapi import Depends, HTTPException, Request

from api.bootstrap import WebRuntime


def runtime(request: Request) -> WebRuntime:
    if request.app.state.runtime is None:
        raise HTTPException(503, '账号和会话服务暂不可用。')
    return request.app.state.runtime


Runtime = Annotated[WebRuntime, Depends(runtime)]


def require_write_origin(request: Request) -> None:
    origin = request.headers.get('origin')
    same_origin = f'{request.url.scheme}://{request.url.netloc}'
    if origin is not None and origin not in (same_origin, *request.app.state.settings.cors_origins):
        raise HTTPException(403, '请求来源未获授权。')
