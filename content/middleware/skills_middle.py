from deepagents.middleware import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig


class WorkspaceSkillsMiddleware(SkillsMiddleware):
    """在每次模型调用前用原生加载器刷新会话和内置 Skill。"""

    name = SkillsMiddleware.__name__
    # 将原生加载时机移到 before_model，避免首轮重复扫描。
    before_agent = AgentMiddleware.before_agent
    abefore_agent = AgentMiddleware.abefore_agent

    def before_model(self, state, runtime, config: RunnableConfig):
        loaded = super().before_agent({**state, 'skills_metadata': None}, runtime, config) or {}
        pinned = super().before_model({**state, **loaded}, runtime) or {}
        return {**loaded, **pinned}

    async def abefore_model(self, state, runtime, config: RunnableConfig):
        loaded = await super().abefore_agent({**state, 'skills_metadata': None}, runtime, config) or {}
        pinned = await super().abefore_model({**state, **loaded}, runtime) or {}
        return {**loaded, **pinned}
