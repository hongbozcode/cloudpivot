from content.all_agent import AllAgent
from core.agent_setting import configure_langsmith


configure_langsmith()
agent = AllAgent().agent
