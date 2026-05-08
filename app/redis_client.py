import redis.asyncio as redis

from app.config import settings


redis_client = redis.from_url(settings.redis_url, decode_responses=True)


async def redis_healthcheck() -> bool:
    try:
        pong = await redis_client.ping()
        return bool(pong)
    except Exception:
        return False
