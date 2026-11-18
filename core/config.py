import os
from pathlib import Path

from dotenv import load_dotenv # 导入load_dotnev(可有可无)
load_dotenv()  # 默认加载启动时当前目录下的 .env 文件 (可有可无)


def require_env(*names: str) -> tuple[str, ...]:
    values = tuple(os.getenv(name, '').strip() for name in names)
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        raise RuntimeError('缺少环境变量：' + ', '.join(missing) + '。请检查 .env.example。')
    return values


LOG_DIR = os.path.dirname(__file__) # 当前脚本所在目录
NEED_CONSOLE_LOG = True # 日志是否打印到控制台

ROOT_PATH_AGENT = Path(os.getenv('AGENT_WORKSPACE_ROOT') or '/agent_files')

BASE_LLM = os.getenv("BASE_LLM") # 从环境变量中获取模型
SMALL_LLM = os.getenv("SMALL_LLM") # 从环境变量中获取模型

MODEL_API_BASE_URL = os.getenv("MODEL_API_BASE_URL") or None

_minio_host, _minio_port, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET_NAME = require_env(
    'HOST_IP', 'MINIO_PORT', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY', 'MINIO_BUCKET_NAME'
)
MINIO_ENDPOINT = f'{_minio_host}:{_minio_port}'
MINIO_CONSOLE_PORT = os.getenv("MINIO_CONSOLE_PORT")

USER_UPLOAD_DIR ='user_upload'
CONVERSATION_HISTORY_PATH = 'conversation_history'
SKILL_DIR_PATH = 'skills'

WAIT_RATE_LIMIT_SEC = 90 # 等待wait_rate_limit的间隔
WAIT_RATE_LIMIT_RETRY = 3 # 重试次数


BASE_VLM = os.getenv("BASE_VLM")  # VLM 模型
IMAGE_MODEL = os.getenv("IMAGE_MODEL") # 基础生图模型
EDIT_IMAGE_MODEL = os.getenv("EDIT_IMAGE_MODEL") # 修改图像模型
GENERATE_IMAGE_PATH = 'generate_images'  # 生成图片保存目录

TAVILY_SEARCH_KEY = os.getenv("TAVILY_SEARCH_KEY", "").strip()
USE_EXCEL = os.getenv("USE_EXCEL", "").strip().lower() == "true"
