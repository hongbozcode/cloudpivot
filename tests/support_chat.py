import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
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
        text = '1' if str(messages[0].content).startswith('你是 CloudPivot 的进度摘要助手') else '这是流式回复。'
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, **kwargs)

    async def _astream(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: Any = None, **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        prompt = next(message.content for message in reversed(messages) if isinstance(message, HumanMessage))
        tool_calls = {
            '请生成文件后等待': ('write_file', '{"file_path":"/report.txt","content":"已生成的内容"}'),
            '请查询时间并回答': ('get_current_time', '{}'),
            '请生成慢速报告': ('write_slow_report', '{}'),
        }
        if prompt in tool_calls and isinstance(messages[-1], HumanMessage):
            name, arguments = tool_calls[prompt]
            yield ChatGenerationChunk(message=AIMessageChunk(content='', tool_call_chunks=[{
                'name': name, 'args': arguments, 'id': f'run-{name}', 'index': 0,
            }]))
            return
        yield ChatGenerationChunk(message=AIMessageChunk(content=[
            {'type': 'reasoning', 'reasoning': 'hidden-reasoning'},
            {'type': 'text', 'text': '这是', 'index': 0},
        ]))
        if prompt == '请让运行失败':
            raise RuntimeError('internal traceback with secret-token')
        await asyncio.sleep(12 if prompt == '请生成文件后等待' else 0.9)
        yield ChatGenerationChunk(message=AIMessageChunk(content=[
            {'type': 'text', 'text': '流式回复。', 'index': 0},
        ]))
