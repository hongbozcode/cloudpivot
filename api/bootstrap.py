import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from sqlalchemy import text

from api.settings import WebSettings
from connection.accounts import AccountStore
from connection.chat import ChatStore
from connection.postgres import open_checkpoint_pool, open_database
from connection.run_cache import RunEvents
from content.chat_runs import ChatRuns, DeepChatAgent


AgentFactory = Callable[[AsyncPostgresSaver], CompiledStateGraph]


def event_loop() -> asyncio.AbstractEventLoop:
    if sys.platform == 'win32':
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def build_agent(checkpointer: AsyncPostgresSaver) -> CompiledStateGraph:
    from content.all_agent import AllAgent

    return AllAgent(checkpointer=checkpointer).agent


@dataclass
class WebRuntime:
    settings: WebSettings
    accounts: AccountStore
    chat: ChatStore
    runs: ChatRuns
    events: RunEvents


@asynccontextmanager
async def open_runtime(settings: WebSettings, agent_factory: AgentFactory) -> AsyncIterator[WebRuntime | None]:
    if not settings.database_url:
        yield None
        return

    async with AsyncExitStack() as stack:
        database = await stack.enter_async_context(open_database(settings.database_url))
        owner = await stack.enter_async_context(database.connect())
        locked = (await owner.execute(text(
            'SELECT pg_try_advisory_lock(1594950900, hashtext(current_schema()))'
        ))).scalar_one()
        await owner.commit()
        if not locked:
            raise RuntimeError('该 CloudPivot 数据库 schema 已由其他 FastAPI 实例使用。')
        stack.push_async_callback(
            owner.execute, text('SELECT pg_advisory_unlock(1594950900, hashtext(current_schema()))'),
        )

        checkpointer = AsyncPostgresSaver(await stack.enter_async_context(open_checkpoint_pool(settings.database_url)))
        await checkpointer.setup()
        chat = ChatStore(database)
        # Legacy tool discovery owns its own event loop and may start a subprocess.
        graph = await asyncio.to_thread(agent_factory, checkpointer)
        await chat.interrupt_pending()
        runs = ChatRuns(chat, DeepChatAgent(graph))
        cache = Redis.from_url(
            settings.redis_url, socket_timeout=0.5, socket_connect_timeout=0.5,
            retry=Retry(NoBackoff(), 0),
        ) if settings.redis_url else None
        if cache is not None:
            stack.push_async_callback(cache.aclose)
        stack.push_async_callback(runs.close)
        yield WebRuntime(settings, AccountStore(database), chat, runs, RunEvents(chat, cache))
