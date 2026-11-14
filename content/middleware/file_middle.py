from typing import Any

from  langchain.agents.middleware  import  AgentMiddleware,AgentState
from langchain_core.messages import ToolMessage

from uuid import uuid4

from utils.doc_utils import os_utils
from core import config as cfg

class FileMiddle(AgentMiddleware):

    def before_model(self, state, runtime):
        # 把目录结构添加到上下文
        #得到目录结构字符串
        dir_str = os_utils.list_directory(path=cfg.ROOT_PATH_AGENT)

        #添加到上下文
        return {'messages':[ToolMessage(content=f"当前目录结构:\n{dir_str}",tool_call_id=uuid4().hex)]}

    pass