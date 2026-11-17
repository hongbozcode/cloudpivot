import asyncio
from openai import RateLimitError
from langchain.agents.middleware import wrap_model_call
from core import config
from utils.general_utils.loggers import logger

@wrap_model_call
async def wait_rate_limit(request,handler):
    attempts = max(1, config.WAIT_RATE_LIMIT_RETRY)
    for attempt in range(1, attempts + 1):
        try:
            return await handler(request)
        except RateLimitError:
            if attempt == attempts:
                raise
            logger.info('触发速率限制，等待重试 %s/%s', attempt, attempts - 1)
            await asyncio.sleep(config.WAIT_RATE_LIMIT_SEC)
