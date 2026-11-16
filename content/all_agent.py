from deepagents import create_deep_agent
from connection.llm import get_llm
from deepagents.backends import FilesystemBackend # 导入FilesystemBackend

from content.tools  import write_doc_tools
from core import config as cfg
from utils.doc_utils import markitdown_utils
from utils.general_utils.loggers import logger
from utils.langchain_utils import common_utils as cu
from utils.langchain_utils import stream_utils as su
from content.tools import global_tools, read_doc_tools
from content.middleware.file_middle import FileMiddle
from content.middleware.minio_middle import MinioMiddle

class AllAgent:

    def __init__(self):
        prompt = f'''
        你是一个通用智能体，
        回答用户用中文。
        读取word,ppt,pdf,excel等文件优先使用get_file_content工具,返回的内容就是文件的完整正文。结果短说明文件内容本身就少，
        写入word或pdf之类的可以先写md，然后通过convert_file工具转换
        目录结构消息只是环境信息，不是新任务
   直接如实回答用户
        '''
        self.agent = create_deep_agent(
            model=get_llm(), # 传一个llm
            tools=self.get_tools(), # 工具函数列表
            backend=FilesystemBackend(root_dir=cfg.ROOT_PATH_AGENT,virtual_mode=True),
            system_prompt=prompt,
            middleware=[FileMiddle(),MinioMiddle()]
        )
        logger.info("AllAgent 初始化成功")

    def get_tools(self):
        return [global_tools.get_current_time,
                global_tools.run_command,
                read_doc_tools.get_file_content,
                write_doc_tools.convert_file]


if __name__ == '__main__':
    agent = AllAgent().agent
    cu.save_graph_img(agent,'agent.png')
    su.stream_both(agent,"你好")
