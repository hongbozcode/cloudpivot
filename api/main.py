from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from sqlalchemy.exc import OperationalError as DatabaseOperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from api import accounts, chat
from api.bootstrap import AgentFactory, build_agent, open_runtime
from api.settings import WebSettings


def create_app(
    *, settings: WebSettings | None = None, agent_factory: AgentFactory = build_agent,
) -> FastAPI:
    settings = settings if settings is not None else WebSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with open_runtime(settings, agent_factory) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(title='CloudPivot', lifespan=lifespan)
    app.state.runtime = None
    app.state.settings = settings
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(settings.cors_origins),
            allow_credentials=True, allow_methods=['GET', 'POST', 'PATCH'],
            allow_headers=['Content-Type'],
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
    return app


app = create_app()
