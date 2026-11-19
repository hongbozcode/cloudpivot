from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from functools import partial
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from connection.chat import ChatStore, CompletedContext, TerminalStatus
from content.middleware.run_actions import RunActions, current_actions
from content.run_artifacts import RunArtifacts
from content.run_progress import RunProgress

logger = logging.getLogger(__name__)


class DeepChatAgent:
    def __init__(self, graph: CompiledStateGraph):
        self.graph = graph

    async def stream_answer(
        self, run_id: UUID, session_id: UUID, text: str,
        context: CompletedContext,
    ) -> AsyncIterator[str]:
        state = {}
        if context.last_completed:
            snapshot = await self.graph.aget_state({'configurable': {'thread_id': str(context.last_completed)}})
            state = dict(snapshot.values)
            if not state and context.legacy_checkpoint_is_current:
                legacy = await self.graph.aget_state({'configurable': {'thread_id': str(session_id)}})
                if not legacy.next:
                    state = dict(legacy.values)
        if not state.get('messages'):
            state['messages'] = [
                (HumanMessage if message['role'] == 'user' else AIMessage)(content=message['text'])
                for message in context.messages
            ]
        state['messages'] = [*state['messages'], HumanMessage(content=text)]
        async with aclosing(self.graph.astream(
            state,
            config={'configurable': {'thread_id': str(run_id), 'workspace_id': str(session_id)}},
            stream_mode='messages', version='v2', durability='sync',
        )) as stream:
            async for part in stream:
                if part['type'] != 'messages':
                    continue
                message, metadata = part['data']
                if metadata.get('langgraph_node') != 'model' or not isinstance(message, AIMessageChunk):
                    continue
                for block in message.content_blocks:
                    if block['type'] == 'text' and block['text']:
                        yield block['text']


class ChatRuns:
    def __init__(self, store: ChatStore, agent: DeepChatAgent, progress_model: BaseChatModel | None):
        self.store = store
        self.agent = agent
        self.progress_model = progress_model
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.stops: dict[UUID, asyncio.Task[None]] = {}

    def start(self, run_id: UUID, session_id: UUID, text: str) -> None:
        if run_id in self.stops:
            return
        task = asyncio.create_task(self._run(run_id, session_id, text))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run_id, None))

    async def stop(self, run_id: UUID) -> None:
        stopping = self.stops.get(run_id)
        if stopping is None:
            stopping = asyncio.create_task(self._stop(run_id))
            self.stops[run_id] = stopping
        await asyncio.shield(stopping)

    async def _stop(self, run_id: UUID) -> None:
        try:
            if task := self.tasks.get(run_id):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self.store.finish(run_id, 'stopped')
        finally:
            self.stops.pop(run_id, None)

    async def close(self) -> None:
        await asyncio.gather(*self.stops.values(), return_exceptions=True)
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.store.interrupt_pending()

    async def _run(self, run_id: UUID, session_id: UUID, text: str) -> None:
        status: TerminalStatus = 'completed'
        actions = RunActions()
        token = current_actions.set(actions)
        artifacts = None
        try:
            if not await self.store.is_running(run_id):
                return
            publish = partial(self.store.append_event, run_id)
            async with asyncio.timeout(300):
                context = await self.store.completed_context(session_id)
                capture = RunArtifacts(run_id, session_id, publish)
                await capture.start()
                artifacts = capture
                actions.progress = RunProgress(self.progress_model, text, publish, artifacts.capture)
                has_answer = False
                async with aclosing(self.agent.stream_answer(run_id, session_id, text, context)) as stream:
                    async for delta in stream:
                        await publish({'type': 'text_delta', 'text': delta})
                        has_answer |= bool(delta.strip())
                if not has_answer:
                    raise ValueError('The assistant returned no displayable text')
        except Exception as exc:
            logger.warning('Agent run %s failed (%s)', run_id, type(exc).__name__)
            status = 'failed'
        finally:
            settlement = asyncio.create_task(self._settle(run_id, actions, artifacts))
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                await settlement
                raise
            finally:
                current_actions.reset(token)
        try:
            await self.store.finish(run_id, status)
        except Exception as exc:
            logger.error('Could not persist run %s status %s (%s)', run_id, status, type(exc).__name__)

    async def _settle(self, run_id: UUID, actions: RunActions, artifacts: RunArtifacts | None) -> None:
        try:
            await actions.drain()
            if artifacts is not None:
                await artifacts.capture()
        except Exception as exc:
            logger.warning('Could not capture artifacts for run %s (%s)', run_id, type(exc).__name__)
