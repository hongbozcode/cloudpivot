from typing import Any, Callable

from  langchain.agents.middleware  import  AgentMiddleware,AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from utils.general_utils.loggers import logger
from uuid import uuid4

from utils.doc_utils import os_utils
from core import config as cfg
from os.path import normpath

class FileState(AgentState):
    before_paths:set


class FileMiddle(AgentMiddleware):
    state_schema = FileState

    def before_model(self, state, runtime):
        # 把目录结构添加到上下文
        #得到目录结构字符串
        dir_str = os_utils.list_directory(path=cfg.ROOT_PATH_AGENT)
        current_paths=os_utils.get_all_files(path=cfg.ROOT_PATH_AGENT)
        logger.info(f"before_paths:{state.get('before_paths')}")
        logger.info(f"current_paths:{current_paths}")
        # 判断是否用添加到上下文
        if not state.get("before_paths") or current_paths!=state["before_paths"]:
            logger.info("添加目录结构到上下文")
            #添加到上下文
            return {'messages':[ToolMessage(content=f"当前目录结构:\n{dir_str}",tool_call_id=uuid4().hex)],"before_paths":current_paths}
        return None

    #不允许展示根目录，进行透明化处理
    async def awrap_tool_call(self,request,handler):
        response =  await handler(request)
        #  替换路径，保证透明化
        content = response.content
        if content and type(content) is str:
            # 统一路径格式
            content = normpath(content)
            response.content=content.replace(cfg.ROOT_PATH_AGENT,'')
        return response