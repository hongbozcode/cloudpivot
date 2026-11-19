from functools import lru_cache

from connection.llm import get_vlm
from content.workspace import get_workspace_root
from utils.doc_utils import base64_utils, os_utils
from langchain.messages import HumanMessage
from langchain_core.tools import tool
from openai import OpenAIError

def read_image_tool(model_name: str | None):
    @lru_cache(maxsize=1)
    def vision_model():
        return get_vlm(model_name)

    @tool
    def read_image(image_path: str):
        """理解工作区图片。image_path 是图片的工作区路径。"""
        return _read_image(image_path, vision_model)

    return read_image


def _read_image(image_path, vision_model):
    try:
        image_path = os_utils.resolve_workspace_path(image_path, get_workspace_root())
        base64_url = base64_utils.file_to_data_uri(image_path)
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        return f'图片读取失败：{exc}'

    messages = [HumanMessage(content=[{"type": "text", "text": "描述下图"},
                                      {"type": "image_url", "image_url": {"url": base64_url}}])]
    try:
        result = vision_model().invoke(messages)
    except OpenAIError:
        return '图片理解服务请求失败。请检查模型配置和服务状态。'
    except RuntimeError as exc:
        return f'图片理解失败：{exc}'
    return result.content
