"""
Per-user timezone support.

The DB stores all timestamps in UTC (timestamptz) and all DATE columns as the
user's *local* calendar date at the moment of writing. To make that correct we
need each user's timezone.

  - tz_name lives in user_settings.tz_name (IANA name, e.g. "Europe/Moscow").
  - Default is UTC until the user sets a zone (via geolocation or a preset).
  - A tiny in-process cache avoids a DB hit on every date computation.

Public:
  await user_zone(uid)            -> ZoneInfo
  await user_tz_name(uid)         -> str
  await set_user_tz(uid, name)    -> None     (upsert + cache)
  await today(uid)               -> date      (user-local calendar date)
  await now_local(uid)           -> datetime  (tz-aware, user-local)
  to_local(dt, zone)             -> datetime  (UTC/naive -> user-local)
  tz_from_coords(lat, lon)       -> str | None (IANA name via timezonefinder)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db.engine import get_session

log = logging.getLogger(__name__)

_DEFAULT_TZ = "UTC"
_cache: dict[str, str] = {}

# timezonefinder is heavy to construct; build lazily and keep one instance.
_tf = None


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


async def user_tz_name(uid: str) -> str:
    if uid in _cache:
        return _cache[uid]
    try:
        async with get_session() as s:
            r = await s.execute(
                text("SELECT tz_name FROM user_settings WHERE user_id = :u"),
                {"u": uid},
            )
            row = r.scalar_one_or_none()
    except Exception as exc:
        log.warning("user_tz_name lookup failed: %s", exc)
        row = None
    name = row or _DEFAULT_TZ
    _cache[uid] = name
    return name


async def user_zone(uid: str) -> ZoneInfo:
    return _zone(await user_tz_name(uid))


async def set_user_tz(uid: str, tz_name: str) -> None:
    # Validate — fall back silently if the name is bogus.
    try:
        ZoneInfo(tz_name)
    except Exception:
        log.warning("set_user_tz: invalid tz %r, ignoring", tz_name)
        return
    async with get_session() as s:
        await s.execute(
            text("""
                INSERT INTO user_settings (user_id, tz_name, updated_at)
                VALUES (:u, :tz, now())
                ON CONFLICT (user_id) DO UPDATE
                  SET tz_name = EXCLUDED.tz_name, updated_at = now()
            """),
            {"u": uid, "tz": tz_name},
        )
    _cache[uid] = tz_name


async def today(uid: str) -> date:
    return datetime.now(await user_zone(uid)).date()


async def now_local(uid: str) -> datetime:
    return datetime.now(await user_zone(uid))


def to_local(dt: datetime, zone: ZoneInfo) -> datetime:
    """Convert a UTC/naive datetime into the given zone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone)


def tz_from_coords(lat: float, lon: float) -> str | None:
    """IANA tz name for a lat/lon via timezonefinder, or None."""
    global _tf
    try:
        if _tf is None:
            from timezonefinder import TimezoneFinder
            _tf = TimezoneFinder()
        return _tf.timezone_at(lat=lat, lng=lon)
    except Exception as exc:
        log.warning("tz_from_coords failed: %s", exc)
        return None
