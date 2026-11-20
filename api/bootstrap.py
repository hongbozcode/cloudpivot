import asyncio
import sys
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from sqlalchemy import text

from connection.accounts import AccountStore
from connection.chat import ChatStore
from connection.files import FileStore
from connection.mcp_connections import McpConnections
from connection.models import PersonalModel, RunModels
from connection.platform import PlatformStore
from connection.postgres import open_checkpoint_pool, open_database
from connection.run_cache import RunEvents
from connection.skills import SkillLibrary
from content.chat_runs import ChatRuns, DeepChatAgent
from core.web_settings import WebSettings


AgentFactory = Callable[[AsyncPostgresSaver], CompiledStateGraph]
ProgressModelFactory = Callable[[], BaseChatModel]


def event_loop() -> asyncio.AbstractEventLoop:
    if sys.platform == 'win32':
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def build_agent(
    checkpointer: AsyncPostgresSaver, *, model: BaseChatModel | None = None,
    vlm: str | PersonalModel | None = None, input_budget: int | None = None,
) -> CompiledStateGraph:
    from content.all_agent import AllAgent

    return AllAgent(checkpointer=checkpointer, model=model, vlm=vlm, input_budget=input_budget).agent


@dataclass
class WebRuntime:
    settings: WebSettings
    accounts: AccountStore
    chat: ChatStore
    runs: ChatRuns
    events: RunEvents
    files: FileStore
    platform: PlatformStore
    mcp: McpConnections
    skills: SkillLibrary


@asynccontextmanager
async def open_runtime(
    settings: WebSettings, agent_factory: AgentFactory, progress_model_factory: ProgressModelFactory | None,
) -> AsyncIterator[WebRuntime | None]:
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
        platform = PlatformStore(database, settings.platform_llms, settings.platform_vlms)
        chat = ChatStore(database, platform)
        await chat.interrupt_pending()

        pinned: OrderedDict[RunModels, tuple[DeepChatAgent, BaseChatModel | None]] = OrderedDict()
        building = asyncio.Lock()

        def configure(models: RunModels) -> tuple[DeepChatAgent, BaseChatModel | None]:
            if agent_factory is build_agent:
                from connection.llm import get_llm

                model = get_llm(models.llm)
                budget = models.llm.input_budget if isinstance(models.llm, PersonalModel) else None
                graph = build_agent(checkpointer, model=model, vlm=models.vlm, input_budget=budget)
                progress = progress_model_factory() if progress_model_factory else model
            else:
                graph = agent_factory(checkpointer)
                progress = progress_model_factory() if progress_model_factory else None
            return DeepChatAgent(graph), progress

        async def select_agent(models: RunModels) -> tuple[DeepChatAgent, BaseChatModel | None]:
            async with building:
                if models not in pinned:
                    # Legacy tool discovery may start a subprocess on its own event loop.
                    pinned[models] = await asyncio.to_thread(configure, models)
                    if len(pinned) > 16:
                        pinned.popitem(last=False)
                pinned.move_to_end(models)
                return pinned[models]

        runs = ChatRuns(chat, select_agent)
        cache = Redis.from_url(
            settings.redis_url, socket_timeout=0.5, socket_connect_timeout=0.5,
            retry=Retry(NoBackoff(), 0),
        ) if settings.redis_url else None
        if cache is not None:
            stack.push_async_callback(cache.aclose)
        stack.push_async_callback(runs.close)
        yield WebRuntime(
            settings, AccountStore(database), chat, runs, RunEvents(chat, cache), FileStore(database), platform,
            McpConnections(database), SkillLibrary(database),
        )
