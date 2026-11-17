import asyncio
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage

from connection import minio_client
from core import config
from utils.doc_utils import os_utils, zip_utils


class TimeState(AgentState):
    start_time: float


class MinioMiddle(AgentMiddleware):
    state_schema = TimeState

    def __init__(self):
        minio_client.create_bucket(config.MINIO_BUCKET_NAME)

    def before_agent(self, state, runtime):
        return {'start_time': time.time()}

    def _publish_archive(self, start_time):
        root = config.ROOT_PATH_AGENT
        if not Path(root).is_dir():
            return None
        latest_time = os_utils.get_latest_modification_time(root)
        if latest_time is None or latest_time <= start_time:
            return None

        object_name = f'agent-files-{uuid4().hex}.zip'
        with TemporaryDirectory(prefix='cloudpivot-export-') as temp_dir:
            archive = Path(temp_dir) / object_name
            zip_path = zip_utils.zip_folder(root, output_path=str(archive))
            return minio_client.upload_file_and_download_url(config.MINIO_BUCKET_NAME, object_name, zip_path)

    async def aafter_agent(self, state, runtime):
        download_url = await asyncio.to_thread(self._publish_archive, state['start_time'])
        if download_url:
            return {'messages': [AIMessage(content=f'[点击下载](<{download_url}>)')]}
        return None
