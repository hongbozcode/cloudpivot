import json
import re
from collections.abc import Awaitable, Callable
from functools import partial
from hashlib import blake2s
from typing import Any
from uuid import UUID, uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.messages.content import create_audio_block, create_file_block, create_image_block, create_text_block
from langchain_core.tools import StructuredTool
from mcp.types import CallToolResult, TextResourceContents

from connection.mcp_connections import RemoteTool
from connection.remote_mcp import RemoteMcpUnavailable, call_tool
from connection.tool_approvals import ToolApprovals
from content.middleware.run_actions import RunActions, current_actions


_SECRET_FIELD = re.compile(r'token|secret|password|passphrase|key|auth|credential|cookie|session.?id|密码|凭据|密钥|令牌', re.I)
_SECRET_TEXT = re.compile(r'https?://|\bbearer\b|-----BEGIN|[0-9A-Za-z_.+/=-]{24,}', re.I)


def _argument_preview(tool: RemoteTool, args: dict[str, Any]) -> dict[str, str]:
    preview = {}
    properties = tool.input_schema.get('properties')
    for name, value in list(args.items())[:12]:
        if len(name) > 64 or not name.isprintable():
            continue
        schema = properties.get(name) if isinstance(properties, dict) else None
        marked_secret = isinstance(schema, dict) and (schema.get('writeOnly') or schema.get('format') == 'password')
        if marked_secret or _SECRET_FIELD.search(name):
            preview[name] = '已隐藏'
        elif value is None or isinstance(value, (int, float, bool)):
            preview[name] = json.dumps(value)
        elif isinstance(value, str) and len(value) <= 100 and value.isprintable() and not _SECRET_TEXT.search(value):
            preview[name] = value
        else:
            preview[name] = '已省略'
    return preview


def _tool_name(tool: RemoteTool) -> str:
    identity = blake2s(tool.connection_id.bytes + tool.name.encode(), digest_size=8).hexdigest()
    name = re.sub(r'[^a-zA-Z0-9_-]', '_', tool.name)[:42]
    return f'mcp_{name}_{identity}'


def _result_content(result: CallToolResult) -> list[dict]:
    content = []
    for block in result.content:
        if block.type == 'text':
            content.append(create_text_block(text=block.text))
        elif block.type == 'image':
            content.append(create_image_block(base64=block.data, mime_type=block.mimeType))
        elif block.type == 'audio':
            content.append(create_audio_block(base64=block.data, mime_type=block.mimeType))
        elif block.type == 'resource_link':
            content.append(create_text_block(text=f'{block.name}: {block.uri}'))
        elif block.type == 'resource':
            resource = block.resource
            if isinstance(resource, TextResourceContents):
                content.append(create_text_block(text=resource.text))
            elif not resource.mimeType:
                content.append(create_text_block(text=f'MCP 返回了未标注类型的资源：{resource.uri}'))
            else:
                mime = resource.mimeType
                create_block = create_image_block if mime.startswith('image/') else create_file_block
                content.append(create_block(base64=resource.blob, mime_type=mime))
    if result.structuredContent is not None:
        content.append(create_text_block(text=json.dumps(result.structuredContent, ensure_ascii=False)))
    return content


class RunRemoteTools:
    def __init__(
        self, tools: tuple[RemoteTool, ...], approvals: ToolApprovals, run_id: UUID,
        publish: Callable[[dict], Awaitable[None]],
    ):
        self.tools = {_tool_name(tool): tool for tool in tools}
        self.declarations = [
            StructuredTool(name=name, description=f'{tool.service} · {tool.title}\n{tool.description}',
                           args_schema=tool.input_schema)
            for name, tool in self.tools.items()
        ]
        self.approvals = approvals
        self.run_id = run_id
        self.publish = publish

    async def invoke(self, tool: RemoteTool, call: dict, actions: RunActions) -> ToolMessage:
        step = uuid4()
        tool_display = tool.name if tool.name == tool.title else f'{tool.title} · {tool.name}'
        label = f'{tool.service} · {tool_display}'
        args = call['args']
        if tool.approval == 'confirm':
            requested = await self.approvals.request(self.run_id, step, {
                'service': tool.service, 'tool': tool_display, 'arguments': _argument_preview(tool, args),
            })
            if not requested or not await self.approvals.wait(self.run_id, step):
                return ToolMessage('用户未批准本次 MCP 工具调用。', tool_call_id=call['id'], status='error')
        await self.publish({'type': 'progress', 'tool_id': str(step), 'summary': label})
        return await actions.execute(partial(self._dispatch, tool, call, label, step))

    async def _dispatch(self, tool: RemoteTool, call: dict, label: str, step: UUID) -> ToolMessage:
        content: str | list[dict] = '远程 MCP 工具调用失败。'
        try:
            result = await call_tool(tool.url, tool.name, call['args'])
            failed = result.isError
            if not failed:
                content = _result_content(result)
        except (RemoteMcpUnavailable, ValueError):
            failed = True
        await self.publish({
            'type': 'tool_result', 'tool_id': str(step), 'label': label,
            'status': 'failed' if failed else 'completed',
        })
        return ToolMessage(
            content,
            tool_call_id=call['id'], status='error' if failed else 'success',
        )


class RemoteToolsMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        actions = current_actions.get()
        if actions and actions.remote_tools:
            request = request.override(tools=[*request.tools, *actions.remote_tools.declarations])
        return await handler(request)

    async def awrap_tool_call(self, request, handler):
        actions = current_actions.get()
        remote = actions.remote_tools if actions else None
        tool = remote.tools.get(request.tool_call['name']) if remote else None
        if actions is not None and remote is not None and tool is not None:
            return await remote.invoke(tool, request.tool_call, actions)
        return await handler(request)
