from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Literal, Protocol
from uuid import UUID

from langchain_core.messages import AIMessageChunk, HumanMessage
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


class ChatAgent(Protocol):
    def stream_answer(self, session_id: UUID, text: str) -> AsyncIterator[str]: ...


class RunWriter(Protocol):
    async def append_text(self, run_id: UUID, text: str) -> None: ...

    async def finish(
        self, run_id: UUID, status: Literal['completed', 'failed', 'interrupted'], answer: str = '',
    ) -> None: ...


class DeepChatAgent:
    def __init__(self, graph: CompiledStateGraph):
        self.graph = graph

    async def stream_answer(self, session_id: UUID, text: str) -> AsyncIterator[str]:
        async for part in self.graph.astream(
            {'messages': [HumanMessage(content=text)]},
            config={'configurable': {'thread_id': str(session_id)}},
            stream_mode='messages', version='v2',
        ):
            if part['type'] != 'messages':
                continue
            message, metadata = part['data']
            if metadata.get('langgraph_node') != 'model' or not isinstance(message, AIMessageChunk):
                continue
            for block in message.content_blocks:
                if block['type'] == 'text' and block['text']:
                    yield block['text']


class ChatRuns:
    def __init__(self, store: RunWriter, agent: ChatAgent):
        self.store = store
        self.agent = agent
        self.tasks: set[asyncio.Task[None]] = set()

    def start(self, run_id: UUID, session_id: UUID, text: str) -> None:
        task = asyncio.create_task(self._run(run_id, session_id, text))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self) -> None:
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, run_id: UUID, session_id: UUID, text: str) -> None:
        answer: list[str] = []
        try:
            async with asyncio.timeout(300):
                async for delta in self.agent.stream_answer(session_id, text):
                    await self.store.append_text(run_id, delta)
                    answer.append(delta)
                if not ''.join(answer).strip():
                    raise ValueError('The assistant returned no displayable text')
            await self.store.finish(run_id, 'completed', ''.join(answer))
        except asyncio.CancelledError:
            await self._record_failure(run_id, 'interrupted')
            raise
        except Exception as exc:
            logger.warning('Agent run %s failed (%s)', run_id, type(exc).__name__)
            await self._record_failure(run_id, 'failed')

    async def _record_failure(self, run_id: UUID, status: Literal['failed', 'interrupted']) -> None:
        try:
            await self.store.finish(run_id, status)
        except Exception as exc:
            logger.error('Could not persist run %s status %s (%s)', run_id, status, type(exc).__name__)
