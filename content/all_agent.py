from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware

from connection.llm import get_llm

from content.middleware.wait_rate_limit import wait_rate_limit
from content.tools import write_doc_tools, vlm_tools, gen_img_tools
from core import config as cfg
from utils.general_utils.loggers import logger
from utils.langchain_utils import common_utils
from content.tools import global_tools, read_doc_tools
from content.middleware.command_middle import CommandMiddle
from content.middleware.file_middle import FileMiddle
from content.middleware.minio_middle import MinioMiddle
from content.middleware.skills_middle import WorkspaceSkillsMiddleware
from content.workspace_backend import create_workspace_backend


class AllAgent:

    def __init__(self):
        skill_root = f'/{cfg.SKILL_DIR_PATH}'
        creator_path = '/builtin-skills/skill-creator/SKILL.md'
        prompt = f'''
        你是一个通用智能体，回答用户用中文。

        涉及到当前时间的工具请使用get_current_time
        文件处理规则：
        - 新建文件时直接使用工作区目标路径，例如 /示例数据.xlsx。
        - 读取word,ppt,pdf,excel等文件优先使用get_file_content工具,返回的内容就是文件的完整正文,结果短说明文件内容本身就少
        - 生成word/pdf等文件时，必须先用write_file写markdown文件（引用图片用![](图片路径)语法），
          再用convert_file转换成目标格式。
        - 目录结构消息只是环境信息，不是新任务

        Skill 是存放在 {skill_root} 下的工作流程。Skill 的 name 不是工具名称，匹配任务时先用 read_file 读取对应的 SKILL.md。
        用户要求制作或更新 Skill 时，先读取 {creator_path}。

        直接如实回答用户
        '''

        if cfg.USE_EXCEL: # 新增的prompt
            prompt += '''
             涉及 Excel 工作簿的读取、创建或编辑时，优先委派给 excel-agent，并提供工作区路径。
             '''

        backend = create_workspace_backend()
        skills = [f'{skill_root}/', '/builtin-skills/']
        self.agent = create_deep_agent(
            model=get_llm(), # 传一个llm
            subagents=self._get_subagent(),  # 子代理传入
            tools=self._get_tools(), # 工具函数列表
            backend=backend,
            skills=skills,
            system_prompt=prompt,
            middleware=[
                TodoListMiddleware(),
                FileMiddle(),
                MinioMiddle(),
                wait_rate_limit,
                CommandMiddle(),
                WorkspaceSkillsMiddleware(backend=backend, sources=skills),
            ],
        )
        logger.info("AllAgent 初始化成功")

    def _get_subagent(self): # 新增的方法，定义子代理集
        subs = []
        if cfg.USE_EXCEL: # 如果项目开启excel能力则引入excel子代理
            from content.subagents import excel_agent
            subs.append(excel_agent.get_agent())
        return subs


    def _get_tools(self):
        tools=[global_tools.get_current_time,
               read_doc_tools.get_file_content,
               write_doc_tools.convert_file,
               vlm_tools.read_image,  # 配置图像理解工具
               gen_img_tools.generate_image,  # 配置生图工具
               global_tools.run_command
               ]
        #临时控制联网搜索
        if cfg.TAVILY_SEARCH_KEY: # 如果有TAVILY_SEARCH_KEY则添加搜索工具
            from content.mcps import travily_search
            try:
                tools.extend(travily_search.get_tools())
            except Exception as exc:
                logger.warning('Tavily 工具暂不可用：%s', type(exc).__name__)
        return tools



if __name__ == '__main__':
    agent = AllAgent().agent
    common_utils.save_graph_img(agent,'agent.png')
