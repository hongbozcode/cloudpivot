import json
import os
from pathlib import Path
from urllib.parse import unquote

import pypandoc
from langchain.tools import tool

from core import config
from utils.doc_utils import os_utils
from utils.general_utils.globle_utils import get_platform


def _resolve_image(target: str, source: Path, root: Path) -> str:
    if target.lower().startswith(('https://', 'http://', 'data:')):
        return target

    path = Path(unquote(target))
    if not path.anchor:
        relative_to_source = os_utils.resolve_within(source.parent / path, root)
        if relative_to_source.is_file():
            return relative_to_source.as_posix()

    image = os_utils.resolve_workspace_path(path, root)
    if not image.is_file():
        raise FileNotFoundError(f'找不到 Markdown 引用的图片：{target}')
    return image.as_posix()


def _markdown_with_resolved_images(source: Path, root: Path) -> str:
    document = json.loads(pypandoc.convert_file(str(source), 'json', format='markdown'))
    nodes = [document]
    while nodes:
        node = nodes.pop()
        if isinstance(node, dict):
            if node.get('t') == 'Image':
                image_target = node['c'][2]
                image_target[0] = _resolve_image(image_target[0], source, root)
            nodes.extend(node.values())
        elif isinstance(node, list):
            nodes.extend(node)
    return json.dumps(document, ensure_ascii=False)


@tool
def convert_file(write_file_path: str, output_format: str) -> str:
    """将工作区内已有文件转换为 txt、md、pdf、html、docx 等格式。

    新内容请先写成 Markdown，再进行转换。图片可用相对路径或工作区虚拟路径引用。
    返回工作区内的结果路径或失败原因。
    """
    if not output_format.isascii() or not output_format.isalnum():
        return '目标格式必须使用格式名，例如 docx、pdf 或 html。'

    root = Path(config.ROOT_PATH_AGENT).resolve()
    output_format = output_format.lower()
    try:
        source = os_utils.resolve_workspace_path(write_file_path, root)
        output = os_utils.resolve_within(source.with_suffix('.' + output_format), root)
    except (TypeError, ValueError, OSError, RuntimeError):
        return '输入路径和转换结果必须位于工作目录内，并且使用有效的文件名。'

    if not source.is_file():
        return f'输入文件不存在：{write_file_path}。请先用 write_file 创建文件或用 ls 确认路径。'
    if output == source:
        return '输入文件已经是目标格式。'

    input_format = source.suffix.lstrip('.').lower()
    target_format = 'plain' if output_format == 'txt' else output_format
    options = ['--resource-path', os.pathsep.join(map(str, (source.parent, root)))]
    if target_format == 'pdf':
        font = 'SimSun' if get_platform() == 0 else 'Noto Sans CJK SC'
        options.extend(['--pdf-engine=tectonic', '-V', f'mainfont={font}', '-V', f'CJKmainfont={font}'])
    elif target_format == 'html':
        options.extend(['--standalone', '--embed-resources', '-M', f'pagetitle={source.stem}'])

    try:
        if input_format in {'md', 'markdown', 'txt'}:
            document = _markdown_with_resolved_images(source, root)
            pypandoc.convert_text(document, target_format, format='json', outputfile=str(output), extra_args=options)
        else:
            pypandoc.convert_file(str(source), target_format, format=input_format, outputfile=str(output), extra_args=options)
    except (OSError, ValueError, RuntimeError) as exc:
        return f'文件转换失败：{exc}\n输入文件：{write_file_path}，目标格式：{output_format}。'

    return os_utils.workspace_path(output, root)
