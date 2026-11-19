import json
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from connection.chat import ChatStore


PAGE_SIZE = 128


class RunEvents:
    def __init__(self, store: ChatStore, cache: Redis | None):
        self.store = store
        self.cache = cache

    async def page(self, run_id: UUID, after: int) -> tuple[bool, list[dict]]:
        # Only cache the first page for terminal runs. Redis is optional and
        # authorization is checked in PostgreSQL before this method is called.
        key = f'cloudpivot:run-events:v1:{run_id}'
        if self.cache is not None and after == 0:
            try:
                if data := await self.cache.get(key):
                    return True, json.loads(data)
            except (RedisError, ValueError):
                pass
        finished, events = await self.store.events(run_id, after, PAGE_SIZE)
        if self.cache is not None and after == 0 and finished:
            try:
                await self.cache.set(key, json.dumps(events, ensure_ascii=False), ex=300)
            except RedisError:
                pass
        return finished, events
