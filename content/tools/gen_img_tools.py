from pathlib import Path
from uuid import uuid4

import requests

from connection import gen_img
from core import config
from utils.doc_utils import base64_utils, download_utils, os_utils


def generate_image(prompt: str, image_size: str, reference_image_path: str | None = None) -> str:
    """根据提示词生图，或用 reference_image_path 指定参考图进行编辑。

    image_size 支持 1328x1328、1664x928、928x1664、1472x1140、1140x1472、
    1584x1056 和 1056x1584。结果为工作区内的图片路径。
    """
    try:
        model, _ = config.require_env(
            'EDIT_IMAGE_MODEL' if reference_image_path else 'IMAGE_MODEL', 'OPENAI_API_KEY'
        )
        root = config.ROOT_PATH_AGENT
        reference_image = None
        if reference_image_path:
            reference = os_utils.resolve_workspace_path(reference_image_path, root)
            reference_image = base64_utils.file_to_data_uri(reference)

        output_dir = os_utils.resolve_within(Path(root) / config.GENERATE_IMAGE_PATH, root)
        image_url = gen_img.gen_img(model, prompt, image_size, reference_image=reference_image)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f'{uuid4().hex}.png'
        download_utils.download_image(image_url, output)
        return os_utils.workspace_path(output, root)
    except requests.HTTPError as exc:
        return f'图像服务请求失败，HTTP {exc.response.status_code}。'
    except requests.RequestException:
        return '图像服务连接失败或请求超时。'
    except (OSError, RuntimeError, ValueError) as exc:
        return f'图像处理失败：{exc}'
