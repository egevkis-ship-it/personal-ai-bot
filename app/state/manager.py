import json
import logging
from enum import Enum
from typing import Any

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


class SessionType(str, Enum):
    WORKOUT = "workout"
    PENDING_CONFIRM = "pending_confirm"
    AWAITING_INPUT = "awaiting_input"


# TTLs in seconds
_TTL = {
    SessionType.WORKOUT: 4 * 3600,        # 4 hours
    SessionType.PENDING_CONFIRM: 30 * 60,  # 30 minutes
    SessionType.AWAITING_INPUT: 10 * 60,   # 10 minutes
}


def _key(user_id: str, session_type: SessionType) -> str:
    return f"session:{user_id}:{session_type.value}"


async def set_session(
    user_id: str,
    session_type: SessionType,
    data: dict[str, Any],
    ttl: int | None = None,
) -> None:
    r = _get_client()
    k = _key(user_id, session_type)
    await r.set(k, json.dumps(data), ex=ttl or _TTL[session_type])


async def get_session(user_id: str, session_type: SessionType) -> dict[str, Any] | None:
    r = _get_client()
    raw = await r.get(_key(user_id, session_type))
    if raw is None:
        return None
    return json.loads(raw)


async def clear_session(user_id: str, session_type: SessionType) -> None:
    r = _get_client()
    await r.delete(_key(user_id, session_type))


async def get_active_session_type(user_id: str) -> SessionType | None:
    """Returns the most specific active session type for the user."""
    for session_type in (
        SessionType.AWAITING_INPUT,
        SessionType.PENDING_CONFIRM,
        SessionType.WORKOUT,
    ):
        if await get_session(user_id, session_type) is not None:
            return session_type
    return None


async def healthcheck() -> bool:
    try:
        await _get_client().ping()
        return True
    except Exception:
        return False
