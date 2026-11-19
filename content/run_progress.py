import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage


TOOL_LABELS = {
    'get_current_time': '查询时间',
    'get_file_content': '读取文件',
    'read_file': '读取文件',
    'ls': '查看工作区',
    'glob': '查找文件',
    'grep': '搜索文件内容',
    'write_file': '写入文件',
    'edit_file': '编辑文件',
    'convert_file': '转换文档',
    'read_image': '分析图像',
    'generate_image': '生成图像',
    'tavily_search': '搜索网页',
    'tavily_extract': '读取网页',
    'write_todos': '更新待办',
    'task': '委派子任务',
    'run_command': '执行命令',
    'execute': '执行命令',
}
TOOL_SUMMARIES = {
    '读取文件': '获取任务所需的文件内容',
    '写入文件': '保存工作区文件',
    '编辑文件': '更新工作区文件',
    '转换文档': '生成目标格式的文档',
    '搜索网页': '查找任务相关的网页资料',
    '更新待办': '记录任务的执行步骤',
    '委派子任务': '将部分工作交给子助手',
}


class RunProgress:
    def __init__(
        self, model: BaseChatModel | None, task: str, publish: Callable[[dict], Awaitable[None]],
        capture_artifacts: Callable[[], Awaitable[None]],
    ):
        self.model = model
        self.task = task
        self.publish = publish
        self.capture_artifacts = capture_artifacts
        self.summary_attempts = 0

    async def started(self, name: str, tool_id: UUID) -> None:
        label = TOOL_LABELS.get(name, '运行工具')
        # Only a validated selection of a public phrase can reach the event log.
        if self.model is None or self.summary_attempts >= 6:
            return
        self.summary_attempts += 1
        options = {'1': label, '2': TOOL_SUMMARIES.get(label, f'为当前任务{label}')}
        try:
            async with asyncio.timeout(2):
                answer = await self.model.ainvoke([
                    SystemMessage(content=(
                        '你是 CloudPivot 的进度摘要助手。根据用户任务和实际发生的工具事件，'
                        '为当前步骤选择最贴切的展示文字。只返回选项编号。'
                        f'已发生的事件：工具开始。1. {options["1"]}。2. {options["2"]}。'
                    )),
                    HumanMessage(content=self.task[:2000]),
                ], config={'callbacks': []}, stream=False)
            summary = options.get(answer.text.strip())
        except Exception:
            return
        if summary:
            await self.publish({
                'type': 'progress', 'tool_id': str(tool_id), 'summary': summary,
            })

    async def returned(self, name: str, tool_id: UUID, output: object) -> None:
        content = output.content if isinstance(output, ToolMessage) else output
        failed = isinstance(output, ToolMessage) and output.status == 'error'
        if name == 'generate_image' and isinstance(content, str):
            failed |= content.startswith(('图像服务', '图像处理失败'))
        detail = None
        if name == 'get_current_time' and isinstance(content, str) and not failed:
            try:
                detail = datetime.strptime(content, '%Y-%m-%d %H:%M:%S').isoformat(sep=' ')
            except ValueError:
                pass
        if not failed:
            await self.capture_artifacts()
        await self.result(name, tool_id, failed=failed, detail=detail)

    async def result(self, name: str, tool_id: UUID, *, failed: bool, detail: str | None = None) -> None:
        await self.publish({
            'type': 'tool_result', 'tool_id': str(tool_id), 'label': TOOL_LABELS.get(name, '运行工具'),
            'status': 'failed' if failed else 'completed', 'detail': detail,
        })
