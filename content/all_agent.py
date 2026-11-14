from deepagents import create_deep_agent
from connection.llm import get_llm
from deepagents.backends import FilesystemBackend # 导入FilesystemBackend
from core import config as cfg
from utils.general_utils.loggers import logger
from utils.langchain_utils import common_utils as cu
from utils.langchain_utils import stream_utils as su
from content.tools import  global_tools
from content.middleware.file_middle import FileMiddle

class AllAgent:

    def __init__(self):
        prompt = f'''
        你是一个通用智能体，
        回答用户用中文。
        '''
        self.agent = create_deep_agent(
            model=get_llm(), # 传一个llm
            tools=[global_tools.get_current_time,global_tools.run_command], # 工具函数列表
            backend=FilesystemBackend(root_dir=cfg.ROOT_PATH_AGENT,virtual_mode=True),
            system_prompt=prompt,
            middleware=[FileMiddle()]
        )
        logger.info("AllAgent 初始化成功")


if __name__ == '__main__':
    agent = AllAgent().agent
    cu.save_graph_img(agent,'agent.png')
    su.stream_both(agent,"你好")
