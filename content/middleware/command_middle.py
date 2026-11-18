import re

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage

from content.tools.global_tools import parse_command, run_command


_COMMAND_REQUEST = re.compile(
    r'(?:请\s*)?(?:帮我\s*)?(?:(?:运行|执行|run_command\s*[:：]|run|execute)\s*)?'
    r'(?P<quote>`?)(?P<command>pwd|uname -s|python --version|ls[^\S\r\n]+[^`\r\n]+?)(?P=quote)[。.!！]?',
    re.IGNORECASE,
)


def _requested_command(state):
    for message in reversed(state.get('messages', [])):
        if isinstance(message, HumanMessage):
            match = _COMMAND_REQUEST.fullmatch(message.text.strip())
            return parse_command(match['command']) if match else None
    return None


class CommandMiddle(AgentMiddleware):
    tools = [run_command]

    async def awrap_model_call(self, request, handler):
        if _requested_command(request.state) is None:
            tools = [tool for tool in request.tools if getattr(tool, 'name', None) != run_command.name]
            request = request.override(tools=tools)
        return await handler(request)

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        if call['name'] != run_command.name:
            return await handler(request)

        command = parse_command(call['args'].get('command'))
        if command is None or command != _requested_command(request.state):
            return self._error(call, '请先明确要求运行受支持的命令，例如「请运行 pwd」或「请运行 ls /skills」。')

        return await handler(request)

    @staticmethod
    def _error(call, content):
        return ToolMessage(content=content, name=run_command.name, tool_call_id=call['id'], status='error')
