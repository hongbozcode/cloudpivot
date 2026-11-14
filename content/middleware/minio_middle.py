import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import StateT
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from langgraph.typing import ContextT

from connection import minio_client
from core import config
from study.codes.langgraph.复现agent import AgentState
from utils.doc_utils import zip_utils, os_utils
import  asyncio
import os

class TimeState(AgentState):
    start_time:float



class MinioMiddle(AgentMiddleware):
    state_schema = TimeState

    def __init__(self):
        minio_client.create_bucket(config.MINIO_BUCKET_NAME)

    def before_agent(self, state, runtime):
        # 返回更新
        return {'start_time': time.time()}

    async def aafter_agent(self, state, runtime) :
        if os_utils.get_latest_modification_time(config.ROOT_PATH_AGENT) > state['start_time']:

            # 压缩成zip
            zip_path = await  asyncio.to_thread(zip_utils.zip_folder, config.ROOT_PATH_AGENT)
            # 上传minio并得到下载链接
            download_url=await asyncio.to_thread(minio_client.upload_file_and_download_url,config.MINIO_BUCKET_NAME, os.path.basename(zip_path), zip_path)
            # 把下载链接添加进AImessage
            # 超链接形式
            download_url=f"[点击下载](<{download_url}>)"
            return {'messages':[AIMessage(content=download_url)]}
        return None
