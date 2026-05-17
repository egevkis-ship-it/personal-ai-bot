from app.db.engine import db_healthcheck
from app.state.manager import healthcheck as redis_healthcheck


async def build_status_text() -> str:
    db_ok = await db_healthcheck()
    redis_ok = await redis_healthcheck()

    return (
        "Статус системы:\n"
        f"Bot: running\n"
        f"PostgreSQL: {'ok' if db_ok else 'error'}\n"
        f"Redis: {'ok' if redis_ok else 'error'}\n"
    )
