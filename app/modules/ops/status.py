from app.db import db_healthcheck
from app.redis_client import redis_healthcheck


async def build_status_text() -> str:
    db_ok = await db_healthcheck()
    redis_ok = await redis_healthcheck()

    return (
        "System status:\n"
        f"Bot: running\n"
        f"PostgreSQL: {'healthy' if db_ok else 'error'}\n"
        f"Redis: {'healthy' if redis_ok else 'error'}\n"
        "Version: v0.7.5-fitness"
    )
