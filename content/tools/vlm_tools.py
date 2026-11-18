from functools import lru_cache

from connection.llm import get_vlm
from content.workspace import get_workspace_root
from utils.doc_utils import base64_utils, os_utils
from langchain.messages import HumanMessage
from openai import OpenAIError

vision_model = lru_cache(maxsize=1)(get_vlm)


def read_image(image_path):
    """
    理解图片的工具
    :param image_path: 图片的路径
    :return:
    """
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
