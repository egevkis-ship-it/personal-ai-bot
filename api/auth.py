"""
Auth for the prototype: Telegram Login Widget + signed session cookie.

No external deps — uses stdlib hmac/hashlib/base64. Multi-user:
each authenticated Telegram user gets their own data keyed by their Telegram id.
Set ALLOWED_TELEGRAM_USER_IDS to restrict; leave empty to allow any Telegram user.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-insecure-secret-change-me")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SESSION_TTL = 30 * 24 * 3600  # 30 days
_AUTH_MAX_AGE = 24 * 3600     # telegram payload must be fresh

_allow_raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED: set[str] = {x.strip() for x in _allow_raw.split(",") if x.strip()}

_INSECURE_SECRET = "dev-insecure-secret-change-me"


def assert_secure_config(is_prod: bool) -> None:
    """Refuse to start in production with an unsafe secret or a missing bot token.
    No-op outside production so local dev keeps working with defaults."""
    if not is_prod:
        return
    if not SESSION_SECRET or SESSION_SECRET == _INSECURE_SECRET:
        raise RuntimeError(
            "SESSION_SECRET is unset or still the insecure default in production. "
            "Set a strong random SESSION_SECRET in the environment before deploying."
        )
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is empty in production. Set it in the environment."
        )


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ── Telegram Login Widget verification ───────────────────────────────────────

def verify_telegram_login(data: dict) -> bool:
    """Validate the signed payload from the Telegram Login Widget."""
    if not BOT_TOKEN:
        return False
    received = str(data.get("hash", ""))
    if not received:
        return False
    pairs = {k: v for k, v in data.items() if k != "hash" and v is not None}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    expected = hmac.new(secret_key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return False
    try:
        if time.time() - int(data.get("auth_date", 0)) > _AUTH_MAX_AGE:
            return False
    except (TypeError, ValueError):
        return False
    return True


def is_allowed(uid: str) -> bool:
    return (not ALLOWED) or (str(uid) in ALLOWED)


# ── session token (HMAC-signed, no deps) ─────────────────────────────────────

def make_session(uid: str) -> str:
    payload = {"uid": str(uid), "exp": int(time.time()) + SESSION_TTL}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_session(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    good = _b64e(hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(good, sig):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return str(payload.get("uid")) or None
