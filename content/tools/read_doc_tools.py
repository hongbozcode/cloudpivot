from langchain_core.tools import tool

from utils.doc_utils import markitdown_utils


@tool
def get_file_content(reade_file_path):
    """
    将本地文件转换为 Markdown 纯文本，用于后续 LLM 处理。
    基于 markitdown 库，支持常见办公文档与文本格式：
    - Office: pptx / docx / xlsx
    - PDF
    - 纯文本 / HTML / CSV / JSON / XML

    注意：
    - 文件不存在或格式无法解析时，markitdown 会抛出异常，由调用方处理。
    :param reade_file_path: 文件路径（str）
    :return: 转换后的文本内容（str）；文件无文本内容时返回空字符串
    """
    return markitdown_utils.get_file_content(reade_file_path)
