import requests
import os


def gen_img(model, prompt,image_size,reference_image=None):
    """
    :param model: 模型
    :param prompt: 提示词
    :param image_size: 图片大小
    :param reference_image: 参考图的base64 uri
    :return:
    """
    url = "https://api.siliconflow.cn/v1/images/generations"
    payload = {
        "model": model,
        "prompt": prompt,
        "image_size": image_size,
        "batch_size": 1
    } # 请求体，用以发送数据，传递参数
    if reference_image:
        payload["image"] = reference_image

    headers = {
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
        "Content-Type": "application/json"
    } # 请求头，用以指定鉴权

    response = requests.post(url, json=payload, headers=headers, timeout=(10, 90))
    response.raise_for_status()
    try:
        image_url = response.json()['images'][0]['url']
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('生图接口未返回有效的图片地址。') from exc
    if not isinstance(image_url, str) or not image_url.startswith(('https://', 'http://')):
        raise RuntimeError('生图接口未返回有效的图片地址。')
    return image_url
