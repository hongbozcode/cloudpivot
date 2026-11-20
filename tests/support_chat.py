import asyncio
import json
from queue import Queue
from collections.abc import AsyncIterator
from typing import Any

import httpx
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
        if prompt == '请使用销售 Skill':
            if isinstance(messages[-1], HumanMessage) and 'sales' in str(messages[0].content):
                tool_calls[prompt] = ('read_file', '{"file_path":"/skills/sales/SKILL.md"}')
            elif 'Check revenue.' in str(messages[-1].content):
                yield ChatGenerationChunk(message=AIMessageChunk(content='已读取迁入的销售 Skill。'))
                return
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


class RemoteToolChat(TestChatModel):
    def bind_tools(self, tools, **kwargs):
        remote_name = next((tool.name for tool in tools if getattr(tool, 'name', '').startswith('mcp_')), None)
        return self.bind(remote_name=remote_name, **kwargs)

    async def _astream(self, messages, stop=None, run_manager=None, *, remote_name=None, **kwargs):
        prompt = next(message.content for message in reversed(messages) if isinstance(message, HumanMessage))
        if prompt != '查询十月销售':
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk
            return
        if remote_name and isinstance(messages[-1], HumanMessage):
            yield ChatGenerationChunk(message=AIMessageChunk(content='', tool_call_chunks=[{
                'name': remote_name, 'args': '{"month":"十月","api_key":"private-test-token"}',
                'id': 'remote-request', 'index': 0,
            }]))
            return
        yield ChatGenerationChunk(message=AIMessageChunk(content='远程工具流程已结束。'))


class ControlledMcp:
    def __init__(self):
        self.calls = []

    async def respond(self, request):
        if request.method != 'POST':
            return httpx.Response(405)
        if request.url.path != '/mcp':
            return httpx.Response(503)
        message = json.loads(await request.aread())
        if 'id' not in message:
            return httpx.Response(202)
        method = message['method']
        if method == 'initialize':
            result = {
                'protocolVersion': message['params']['protocolVersion'], 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'Controlled MCP', 'version': '1'},
            }
        elif method == 'tools/list':
            result = {'tools': [{
                'name': 'sales', 'description': '按月读取销售记录。',
                'inputSchema': {'type': 'object', 'properties': {
                    'month': {'type': 'string'}, 'api_key': {'type': 'string', 'writeOnly': True},
                }},
            }]}
        elif method == 'tools/call':
            self.calls.append(message['params'])
            result = {'content': [{'type': 'text', 'text': '销售记录'}]}
        else:
            return httpx.Response(400)
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': message['id'], 'result': result})


def listen_for_event(client, app, path, event_type):
    """Read one live SSE event without TestClient buffering the complete response."""
    events = Queue()

    async def subscribe():
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await asyncio.Future()

        async def send(message):
            if message['type'] == 'http.response.start' and message['status'] != 200:
                events.put({'http_status': message['status']})
            if message['type'] == 'http.response.body':
                for line in message['body'].decode().splitlines():
                    if line.startswith('data: '):
                        event = json.loads(line[6:])
                        if event['type'] == event_type:
                            events.put(event)

        await app({
            'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'method': 'GET',
            'scheme': 'http', 'http_version': '1.1', 'path': path, 'query_string': b'',
            'headers': [(b'host', b'testserver'), (b'cookie', cookie)],
        }, receive, send)

    cookie = client.build_request('GET', path).headers['cookie'].encode()
    listener = client.portal.start_task_soon(subscribe)
    try:
        return events.get(timeout=8)
    finally:
        listener.cancel()
