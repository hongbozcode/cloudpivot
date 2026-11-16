import asyncio
import os
from  langchain.agents.middleware  import  AgentMiddleware,AgentState
from langchain_core.messages import ToolMessage
from utils.general_utils.loggers import logger
from uuid import uuid4

from utils.doc_utils import os_utils, base64_utils
from core import config
from os.path import normpath


class FileState(AgentState):
    before_paths:set
    upload_files: list # 存储上传的文件


class FileMiddle(AgentMiddleware):
    state_schema = FileState

    async def abefore_agent(self, state, runtime):
        state_dict = {} # 初始化共享工作目录的文件状态

        if os.path.exists(config.ROOT_PATH_AGENT):
            state_dict['before_paths'] = os_utils.get_all_files(config.ROOT_PATH_AGENT)

        # 接收上传文件
        upload_files = state.get('upload_files')
        if upload_files: # 如果有上传文件,则需下载，之后更新messages，与清空upload_files
            upload_dir = os.path.join(config.ROOT_PATH_AGENT, config.USER_UPLOAD_DIR)  # 获取上传文件的目录路径
            await asyncio.to_thread(os.makedirs,upload_dir, exist_ok=True) # 创建上传文件的目录

            content = '用户上传了文件, 路径如下: '
            for file in upload_files:
                base64_str = file.get('data')
                file_name = file.get('metadata').get('filename')
                # 把Base64内容保存到共享工作目录下的user_upload目录
                file_path = os.path.join(upload_dir, file_name)
                relative_file_path = os.path.join(config.USER_UPLOAD_DIR,file_name)
                content += f'\n{relative_file_path}'
                base64_utils.base64_to_file(base64_str,file_path)
            state_dict['messages'] = [ToolMessage(content=content,tool_call_id=uuid4().hex)]
            state_dict['upload_files'] = None

        return state_dict

    def before_model(self, state, runtime):
        # 把目录结构添加到上下文
        #得到目录结构字符串
        dir_str = os_utils.list_directory(path=config.ROOT_PATH_AGENT)
        current_paths=os_utils.get_all_files(path=config.ROOT_PATH_AGENT)
        logger.info(f"before_paths:{state.get('before_paths')}")
        logger.info(f"current_paths:{current_paths}")
        # 判断是否用添加到上下文
        if not state.get("before_paths") or current_paths!=state["before_paths"]:
            logger.info("添加目录结构到上下文")
            #添加到上下文
            return {'messages':[ToolMessage(content=f"当前目录结构:\n{dir_str}",tool_call_id=uuid4().hex)],"before_paths":current_paths}
        return None

    def change_file_path(self,path):
        """
           转换文件路径为共享工作目录中的文件路径
            # 例如：/user_upload/a.txt -> /agent_files/user_upload/a.txt
           Args:
               path: 原始文件路径
           Returns:
               str: 转换后的文件路径
        """
        root_dir = config.ROOT_PATH_AGENT
        path = os.path.normpath(path)  # 规范化这个路径本身
        if root_dir in path:  # 如果路径本身就包含了根目录，则直接返回
            return path
        if os.path.isabs(path):  # 判断路径是否是绝对路径
            path = os.path.relpath(path, start='/')  # 获取相对路径
        return os.path.normpath(os.path.join(root_dir, path))  # 与真正的工作目录路径拼接


    #不允许展示根目录，进行透明化处理
    async def awrap_tool_call(self,request,handler):

        for  arg_key  in  request.tool_call['args']:
            # 将不在文件管理后端的工具中，加上根目录前缀
            if arg_key in ['reade_file_path']:
                request.tool_call['args'][arg_key] = self.change_file_path(request.tool_call['args'][arg_key])
                pass


        response =  await handler(request)
        #  替换路径，保证透明化
        content = response.content
        if content and type(content) is str:
            # 统一路径格式
            content = normpath(content)
            response.content=content.replace(config.ROOT_PATH_AGENT,'')
        return response