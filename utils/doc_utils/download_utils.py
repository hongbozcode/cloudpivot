from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

def download_image(image_url, save_path):
    response = requests.get(image_url, timeout=(10, 45))
    response.raise_for_status()
    image_data = response.content
    try:
        with Image.open(BytesIO(image_data)) as decoded:
            decoded.verify()
            extension = Image.EXTENSION.get(Path(save_path).suffix.lower())
            if decoded.format == extension:
                Path(save_path).write_bytes(image_data)
                return
        with Image.open(BytesIO(image_data)) as decoded:
            decoded.save(save_path)
    except (UnidentifiedImageError, SyntaxError) as exc:
        raise ValueError('下载响应不是有效的图片。') from exc
