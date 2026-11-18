import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote

import pypandoc
from langchain.tools import tool

from content.workspace import get_workspace_root
from utils.doc_utils import base64_utils, os_utils
from utils.general_utils.globle_utils import get_platform


def _resolve_image(target: str, source: Path, root: Path, media: Path) -> str:
    if target.lower().startswith(('https://', 'http://', 'data:')):
        return target

    path = Path(unquote(target))
    if path.is_relative_to(media):
        return base64_utils.file_to_data_uri(os_utils.resolve_within(path, media))

    if not path.anchor:
        relative_to_source = os_utils.resolve_within(source.parent / path, root)
        if relative_to_source.is_file():
            return base64_utils.file_to_data_uri(relative_to_source)

    image = os_utils.resolve_workspace_path(path, root)
    if not image.is_file():
        raise FileNotFoundError(f'找不到 Markdown 引用的图片：{target}')
    return base64_utils.file_to_data_uri(image)


def _pandoc_document(source: Path, root: Path, input_format: str, *, pdf: bool) -> str:
    with TemporaryDirectory(prefix='cloudpivot-pandoc-') as temp_dir:
        media = Path(temp_dir)
        document = json.loads(pypandoc.convert_file(
            str(source), 'json', format=input_format,
            extra_args=['--sandbox', f'--extract-media={media}'],
        ))
        if pdf:
            document['meta'] = {
                name: value for name, value in document['meta'].items()
                if name in {'title', 'subtitle', 'author', 'date'}
            }
        nodes = [document]
        while nodes:
            node = nodes.pop()
            if isinstance(node, dict):
                kind = node.get('t')
                if kind == 'Image':
                    image_target = node['c'][2]
                    image_target[0] = _resolve_image(image_target[0], source, root, media)
                if pdf and (kind in {'RawBlock', 'RawInline'} or (
                    kind == 'Math' and any(char in node['c'][1] for char in ('\\', '^^'))
                )):
                    raise ValueError('PDF 转换请使用普通 Markdown 和不含原始 TeX 命令的公式。')
                nodes.extend(node.values())
            elif isinstance(node, list):
                nodes.extend(node)
    return json.dumps(document, ensure_ascii=False)


@tool
def convert_file(write_file_path: str, output_format: str) -> str:
    """将工作区内已有文件转换为 txt、md、pdf、html、docx 等格式。

    新内容请先写成 Markdown，再进行转换。图片可用相对路径或工作区虚拟路径引用。
    PDF 请使用普通 Markdown 和不含原始 TeX 命令的公式。
    返回工作区内的结果路径或失败原因。
    """
    if not output_format.isascii() or not output_format.isalnum():
        return '目标格式必须使用格式名，例如 docx、pdf 或 html。'

    output_format = output_format.lower()
    try:
        root = get_workspace_root()
        source = os_utils.resolve_workspace_path(write_file_path, root)
        output = os_utils.resolve_within(source.with_suffix('.' + output_format), root)
    except (TypeError, ValueError, OSError, RuntimeError):
        return '输入路径和转换结果必须位于工作目录内，并且使用有效的文件名。'

    if not source.is_file():
        return f'输入文件不存在：{write_file_path}。请先用 write_file 创建文件或用 ls 确认路径。'
    if output == source:
        return '输入文件已经是目标格式。'

    input_format = source.suffix.lstrip('.').lower()
    if input_format in {'md', 'txt'}:
        input_format = 'markdown'
    target_format = 'plain' if output_format == 'txt' else output_format
    options = ['--sandbox']
    if target_format == 'pdf':
        font = 'SimSun' if get_platform() == 0 else 'Noto Sans CJK SC'
        options.extend([
            '--pdf-engine=tectonic', '--pdf-engine-opt=--untrusted',
            '-V', f'mainfont={font}', '-V', f'CJKmainfont={font}',
        ])
    elif target_format == 'html':
        options.extend(['--standalone', '--embed-resources', '-M', f'pagetitle={source.stem}'])

    try:
        document = _pandoc_document(source, root, input_format, pdf=target_format == 'pdf')
        pypandoc.convert_text(document, target_format, format='json', outputfile=str(output), extra_args=options)
    except (OSError, ValueError, RuntimeError) as exc:
        return f'文件转换失败：{exc}\n输入文件：{write_file_path}，目标格式：{output_format}。'

    return os_utils.workspace_path(output, root)
