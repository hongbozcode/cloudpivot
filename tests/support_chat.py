import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class TestChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return 'cloudpivot-test-chat'

    def bind_tools(self, tools, **kwargs):
        return self.bind(**kwargs)

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: Any = None, **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='这是流式回复。'))])

    async def _astream(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: Any = None, **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content=[
            {'type': 'reasoning', 'reasoning': 'hidden-reasoning'},
            {'type': 'text', 'text': '这是', 'index': 0},
        ]))
        await asyncio.sleep(0.9)
        yield ChatGenerationChunk(message=AIMessageChunk(content=[
            {'type': 'text', 'text': '流式回复。', 'index': 0},
        ]))
