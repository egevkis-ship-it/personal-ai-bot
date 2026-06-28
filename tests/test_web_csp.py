"""
Regression guard for the Content-Security-Policy header (phase 5.3).

The Telegram Login Widget (telegram-widget.js) parses its `data-onauth`
attribute through `__parseFunction()`, which calls `eval()` during init. A
`script-src` without `'unsafe-eval'` makes the browser block that eval, the
widget init aborts, and the OAuth iframe is never created — the login button
silently fails to render. This test pins the CSP so that regression can't ship
again.

No DB needed: `/api/config` is a PUBLIC, DB-free route, and the security_headers
middleware decorates every response — so a plain TestClient request (no lifespan)
is enough to read the header.
"""
import os

import pytest

# api.main reads config/env at import — set a benign dev environment first.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("OWNER_TELEGRAM_USER_ID", "local")
os.environ.setdefault("DEV_UID", "local")
os.environ.setdefault("AI_DAILY_LIMIT", "100")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture(scope="module")
def csp() -> str:
    from fastapi.testclient import TestClient

    import api.main as m

    # No `with` → lifespan (DB schema bootstrap) does not run; /api/config is DB-free.
    resp = TestClient(m.app).get("/api/config")
    assert resp.status_code == 200
    header = resp.headers.get("content-security-policy")
    assert header, "CSP header is missing"
    return header


def _directive(csp: str, name: str) -> str:
    for part in csp.split(";"):
        part = part.strip()
        if part.split(" ", 1)[0] == name:
            return part
    raise AssertionError(f"directive {name!r} not found in CSP: {csp!r}")


def test_script_src_allows_telegram_widget_eval(csp: str):
    """telegram-widget.js needs eval() + the script origin to build the widget."""
    script_src = _directive(csp, "script-src")
    assert "'unsafe-eval'" in script_src, (
        "telegram-widget.js calls eval() during init; without 'unsafe-eval' the "
        f"login button never renders. script-src={script_src!r}"
    )
    assert "https://telegram.org" in script_src


def test_frame_src_allows_oauth_iframe(csp: str):
    """The widget injects an <iframe src=https://oauth.telegram.org/embed/...>."""
    frame_src = _directive(csp, "frame-src")
    assert "https://oauth.telegram.org" in frame_src
