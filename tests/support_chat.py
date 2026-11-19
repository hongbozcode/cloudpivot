import asyncio
import json
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
        if isinstance(prompt, str) and prompt.startswith('汇总上传的销售表'):
            if isinstance(messages[-1], HumanMessage):
                path = prompt.splitlines()[-1].removeprefix('- ')
                tool_calls[prompt] = ('get_file_content', json.dumps({'reade_file_path': path}))
            elif messages[-1].name == 'get_file_content' and '128000' in str(messages[-1].content):
                directory = prompt.splitlines()[-1].removeprefix('- ').rsplit('/', 1)[0]
                tool_calls[prompt] = ('write_file', json.dumps({
                    'file_path': f'{directory}/地区汇总.csv', 'content': '地区,销售额\n华东,128000\n',
                }))
        if isinstance(prompt, str) and prompt.startswith('修改上传的销售表') and isinstance(messages[-1], HumanMessage):
            path = prompt.splitlines()[-1].removeprefix('- ')
            tool_calls[prompt] = ('edit_file', json.dumps({
                'file_path': path, 'old_string': '128000', 'new_string': '96000',
            }))
        if isinstance(prompt, str) and prompt in tool_calls and (
            isinstance(messages[-1], HumanMessage) or messages[-1].name == 'get_file_content'
        ):
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


class TestPlatformChat(TestChatModel):
    model_id: str

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        prompt = next(message.content for message in reversed(messages) if isinstance(message, HumanMessage))
        if isinstance(prompt, str) and prompt.startswith('请识别图片'):
            if isinstance(messages[-1], HumanMessage):
                await asyncio.sleep(0.5)
                path = prompt.splitlines()[-1].removeprefix('- ')
                yield ChatGenerationChunk(message=AIMessageChunk(content='', tool_call_chunks=[{
                    'name': 'read_image', 'args': json.dumps({'image_path': path}), 'id': 'vision-request', 'index': 0,
                }]))
                return
            answer = f'{self.model_id}：{messages[-1].content}'
        else:
            answer = f'当前文本模型 {self.model_id}'
        yield ChatGenerationChunk(message=AIMessageChunk(content=answer))


class TestPlatformVision(TestChatModel):
    model_id: str

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        image = messages[-1].content[1]['image_url']['url']
        if not image.startswith('data:image/png;base64,'):
            raise ValueError('Expected an uploaded PNG')
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f'视觉模型 {self.model_id} 读取了 PNG'))])
