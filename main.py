from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from sqlalchemy.exc import OperationalError as DatabaseOperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from api import accounts, chat, files, mcp, models, platform, skills, workspace_imports
from api.bootstrap import AgentFactory, ProgressModelFactory, build_agent, open_runtime
from core.agent_setting import configure_langsmith
from core.web_settings import WebSettings


def create_app(
    *, settings: WebSettings | None = None, agent_factory: AgentFactory = build_agent,
    progress_model_factory: ProgressModelFactory | None = None,
) -> FastAPI:
    settings = settings if settings is not None else WebSettings()
    configure_langsmith()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with open_runtime(settings, agent_factory, progress_model_factory) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(title='CloudPivot', lifespan=lifespan)
    app.state.runtime = None
    app.state.settings = settings
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(settings.cors_origins),
            allow_credentials=True, allow_methods=['GET', 'POST', 'PATCH'],
            allow_headers=['Content-Type'], expose_headers=[files.PDF_PAGES_HEADER],
        )

    @app.middleware('http')
    async def prevent_response_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers.setdefault('Cache-Control', 'no-store')
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse({'detail': '提交的数据不符合要求，请检查后重试。'}, status_code=422)

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeout)
    @app.exception_handler(DatabaseOperationalError)
    @app.exception_handler(DatabaseTimeout)
    async def database_unavailable(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({'detail': '服务暂不可用，请稍后重试。'}, status_code=503)

    @app.get('/api/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    app.include_router(accounts.router)
    app.include_router(chat.router)
    app.include_router(files.router)
    app.include_router(platform.router)
    app.include_router(models.router)
    app.include_router(mcp.router)
    app.include_router(skills.router)
    app.include_router(skills.session_router)
    app.include_router(workspace_imports.router)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host='127.0.0.1',
        port=app.state.settings.uvicorn_port,
        loop='api.bootstrap:event_loop',
    )
