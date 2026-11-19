from functools import lru_cache
from math import ceil

from langchain.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import tool
from openai import OpenAIError
from PIL import Image

from connection.llm import get_vlm
from connection.models import PersonalModel
from content.workspace import get_workspace_root
from utils.doc_utils import base64_utils, os_utils


IMAGE_PROMPT = '描述下图'


def read_image_tool(model_name: str | PersonalModel | None):
    @lru_cache(maxsize=1)
    def vision_model():
        return get_vlm(model_name)

    @tool
    def read_image(image_path: str):
        """理解工作区图片。image_path 是图片的工作区路径。"""
        budget = model_name.input_budget if isinstance(model_name, PersonalModel) else None
        return _read_image(image_path, vision_model, budget)

    return read_image


def _read_image(image_path, vision_model, budget: int | None = None):
    try:
        image_path = os_utils.resolve_workspace_path(image_path, get_workspace_root())
        if budget is not None:
            with Image.open(image_path) as image:
                # Estimate image tokens by dimensions. Tiling varies by provider.
                image_tokens = ceil(image.width / 32) * ceil(image.height / 32)
            if image_tokens + count_tokens_approximately([HumanMessage(content=IMAGE_PROMPT)]) > budget:
                return '图片理解输入超过配置的预算。'
        base64_url = base64_utils.file_to_data_uri(image_path)
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        return f'图片读取失败：{exc}'

    messages = [HumanMessage(content=[{"type": "text", "text": IMAGE_PROMPT},
                                      {"type": "image_url", "image_url": {"url": base64_url}}])]
    try:
        result = vision_model().invoke(messages)
    except OpenAIError:
        return '图片理解服务请求失败。请检查模型配置和服务状态。'
    except RuntimeError as exc:
        return f'图片理解失败：{exc}'
    return result.content
