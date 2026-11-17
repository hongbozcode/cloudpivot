from connection.llm import get_vlm
from core import config
from utils.doc_utils import base64_utils, os_utils
from langchain.messages import HumanMessage

vlm = get_vlm()
def read_image(image_path):
    """
    理解图片的工具
    :param image_path: 图片的路径
    :return:
    """
    try:
        image_path = os_utils.resolve_within(image_path, config.ROOT_PATH_AGENT)
    except (TypeError, ValueError, OSError, RuntimeError):
        return '图片路径必须位于工作目录内。'
    base64_url = base64_utils.file_to_data_uri(image_path)

    messages = [HumanMessage(content=[{"type": "text", "text": "描述下图"},
                                      {"type": "image_url", "image_url": {"url": base64_url}}])]
    result = vlm.invoke(messages)
    return result.content
