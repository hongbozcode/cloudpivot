from langchain_core.tools import tool

from core import config
from utils.doc_utils import markitdown_utils, os_utils


@tool
def get_file_content(reade_file_path):
    """
    将本地文件转换为 Markdown 纯文本，用于后续 LLM 处理。
    基于 markitdown 库，支持常见办公文档与文本格式：
    - Office: pptx / docx / xlsx
    - PDF
    - 纯文本 / HTML / CSV / JSON / XML

    注意：
    返回内容即为文件全部正文，短结果代表文件本身内容就少，请勿怀疑解析结果或重复读取
    - 文件不存在或格式无法解析时，markitdown 会抛出异常，由调用方处理。
    :param reade_file_path: 文件路径（str）
    :return: 转换后的文本内容（str）；文件无文本内容时返回空字符串
    """
    try:
        path = os_utils.resolve_workspace_path(reade_file_path, config.ROOT_PATH_AGENT)
        return markitdown_utils.get_file_content(str(path))
    except Exception as e:
        return f"文件读取失败: {e}。文件可能不存在、为空或格式损坏,请勿重复尝试。"
