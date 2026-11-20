import asyncio
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage

from connection import minio_client
from content.middleware.run_actions import current_actions
from content.workspace import WORKSPACE_NAMESPACE, get_workspace_root
from core import agent_setting as config
from utils.doc_utils import os_utils, zip_utils


class TimeState(AgentState):
    start_time: float


class MinioMiddle(AgentMiddleware):
    state_schema = TimeState

    def before_agent(self, state, runtime):
        return {'start_time': time.time()}

    def _publish_archive(self, start_time, *, exclude_session_skills=False):
        root = get_workspace_root()
        if not root.is_dir():
            return None
        excluded_paths = (config.USER_UPLOAD_DIR, config.CONVERSATION_HISTORY_PATH)
        if exclude_session_skills:
            excluded_paths += (config.SKILL_DIR_PATH,)
        latest_time = os_utils.get_latest_modification_time(root, exclude=excluded_paths)
        if latest_time is None or latest_time <= start_time:
            return None

        object_name = f'agent-files-{uuid4().hex}.zip'
        bucket_name = (config.settings.minio_bucket_name or '').strip()
        with TemporaryDirectory(prefix='cloudpivot-export-') as temp_dir:
            archive = Path(temp_dir) / object_name
            zip_path = zip_utils.zip_folder(root, output_path=str(archive), exclude=excluded_paths)
            minio_client.create_bucket(bucket_name)
            return minio_client.upload_file_and_download_url(
                bucket_name, f'{WORKSPACE_NAMESPACE}/{root.name}/{object_name}', zip_path
            )

    async def aafter_agent(self, state, runtime):
        actions = current_actions.get()

        def publish():
            return asyncio.to_thread(self._publish_archive, state['start_time'], exclude_session_skills=actions is not None)

        download_url = await (actions.execute(publish) if actions else publish())
        if download_url:
            return {'messages': [AIMessage(content=f'[点击下载](<{download_url}>)')]}
        return None
