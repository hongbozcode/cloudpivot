from hashlib import sha256
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config

from core import config
from utils.doc_utils.os_utils import is_link


WORKSPACE_NAMESPACE = 'sessions-v1'


def get_workspace_root(run_config: RunnableConfig | None = None) -> Path:
    """Return this run's physical workspace without creating it."""
    if run_config is None:
        try:
            run_config = get_config()
        except RuntimeError:
            run_config = {}

    configurable = run_config.get('configurable', {})
    thread_id = configurable.get('workspace_id', configurable.get('thread_id'))
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError('请先创建或选择会话。运行必须提供 configurable.thread_id。')

    # Keep pre-isolation files separate and avoid special names, path separators
    # and case aliases across Windows and POSIX filesystems.
    sessions = Path(config.ROOT_PATH_AGENT).resolve() / WORKSPACE_NAMESPACE
    root = sessions / sha256(thread_id.encode('utf-8')).hexdigest()
    if any(is_link(path, missing=False) for path in (sessions, root)):
        raise ValueError('会话工作目录必须使用常规目录。')
    return root
