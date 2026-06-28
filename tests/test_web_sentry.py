"""
Phase A monitoring guard: Sentry must be a strict no-op unless SENTRY_DSN is set.

The dangerous regression is the inverse of "errors don't reach Sentry": an app
that *fails to start* (or starts phoning home) when no DSN is configured. This
test pins the no-op path — importing the API with no SENTRY_DSN must leave the
Sentry client inactive and must not raise. The DSN-on path (client active,
release/environment populated) is verified out-of-process during development
since sentry_sdk.init runs once at import and can't be re-flipped in-process.
"""
import os

# Ensure a benign dev env and explicitly NO SENTRY_DSN before importing api.main.
os.environ.pop("SENTRY_DSN", None)
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("OWNER_TELEGRAM_USER_ID", "local")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


def test_sentry_is_noop_without_dsn():
    import sentry_sdk

    import api.main as m  # imports cleanly with no DSN

    assert m.SENTRY_DSN == "", "test env must have no SENTRY_DSN"
    client = sentry_sdk.get_client()
    # No DSN → no active client → nothing is ever sent.
    assert not (client and client.is_active()), "Sentry must be inactive without SENTRY_DSN"
