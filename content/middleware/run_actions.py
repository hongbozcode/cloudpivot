import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware

from content.run_progress import RunProgress

if TYPE_CHECKING:
    from content.middleware.remote_tools import RunRemoteTools

T = TypeVar('T')


class RunActions:
    """Let an already started tool settle before another run uses the workspace."""

    def __init__(self):
        self.accepting = True
        self.progress: RunProgress | None = None
        self.remote_tools: RunRemoteTools | None = None
        self.pending: set[asyncio.Future] = set()

    async def execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if not self.accepting:
            raise asyncio.CancelledError
        task = asyncio.ensure_future(operation())
        self.pending.add(task)
        task.add_done_callback(self._done)
        return await asyncio.shield(task)

    def _done(self, task: asyncio.Future) -> None:
        self.pending.discard(task)
        if not task.cancelled():
            task.exception()

    async def drain(self) -> None:
        self.accepting = False
        await asyncio.gather(*self.pending, return_exceptions=True)


current_actions: ContextVar[RunActions | None] = ContextVar('run_actions', default=None)


class RunActionsMiddleware(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        actions = current_actions.get()
        if actions is None:
            return await handler(request)
        name = request.tool_call['name']
        tool_id = uuid4()
        progress = actions.progress
        if not actions.accepting:
            raise asyncio.CancelledError
        if progress is not None:
            await progress.started(name, tool_id)

        async def invoke():
            try:
                output = await handler(request)
            except Exception:
                if progress is not None:
                    await progress.result(name, tool_id, failed=True)
                raise
            if progress is not None:
                await progress.returned(name, tool_id, output)
            return output

        if name == 'task':
            # Subagents can stop between their own tool calls.
            return await invoke()
        return await actions.execute(invoke)
