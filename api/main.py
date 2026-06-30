"""
Local / cloud prototype API — FastAPI thin layer over the existing bot code.

Auth: Telegram Login Widget -> signed session cookie (see api/auth.py).
Multi-user: every request is scoped to the authenticated Telegram id (uid),
so the app shares the same data as the bot (same user_id column).

Reuses:
  - app.db.*                              (workouts / sets / plans / measurements)
  - app.bot.services.set_parser           (free-text / voice set parsing, no network)
  - app.bot.services.measurement_parser   (text parsing, lazy import)
  - app.modules.fitness.exercise_normalizer (catalog, search — no network)
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import os
import uuid
from datetime import date, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text

import app.db as db
from app.db.engine import engine, get_session
from app.bot.services import tz
from app.bot.services.set_parser import parse_exercise_input

from api.schema import CREATE_SQL
from api import auth

APP_ENV = os.getenv("APP_ENV", "development")
IS_PROD = APP_ENV == "production"
WEB_ORIGIN = os.getenv("WEB_ORIGIN", "http://localhost:8000")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")
ALLOW_SEED = (not IS_PROD) or os.getenv("SEED") == "1"
OWNER_UID = os.getenv("OWNER_TELEGRAM_USER_ID", "").strip()
try:
    AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "100"))
except ValueError:
    AI_DAILY_LIMIT = 100
PHOTO_DIR = os.getenv("PHOTO_DIR", "/data/photos")  # mount a persistent volume here
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
COOKIE = "session"

# Fail loudly at import (container start) on insecure production config.
auth.assert_secure_config(IS_PROD)

log = logging.getLogger("api")

# ── Error monitoring (Sentry) — phase A ──────────────────────────────────────
# No-op unless SENTRY_DSN is set: with no DSN nothing is initialised and nothing
# is ever sent. With a DSN, sentry-sdk auto-instruments FastAPI/Starlette and
# reports unhandled endpoint exceptions. errors-only (no tracing overhead).
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
# Coolify/most CIs expose the built commit under one of these — best-effort.
GIT_SHA = (
    os.getenv("SOURCE_COMMIT")
    or os.getenv("COOLIFY_GIT_COMMIT_SHA")
    or os.getenv("GIT_COMMIT_SHA")
    or os.getenv("GIT_SHA")
    or ""
).strip()
if SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=APP_ENV,
        release=GIT_SHA or None,
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    log.info("Sentry monitoring enabled (env=%s release=%s)", APP_ENV, GIT_SHA or "unknown")
else:
    log.info("Sentry monitoring disabled (no SENTRY_DSN)")

PUBLIC = ("/api/auth/telegram", "/api/auth/logout", "/api/config", "/healthz", "/healthz/ai")

# DB-1: the merged v2 catalog (14 groups, no duplicate labels) drives the picker,
# while folding legacy names in as aliases so history keeps resolving.
from app.bot.services.catalog_v2 import GROUPS as GROUP_RU, GROUP_ORDER, CATALOG, NAME_INDEX  # noqa: E402
from app.bot.services.catalog_v2 import search as _catalog_search  # noqa: E402

_NAME_TO_KEY = NAME_INDEX
_KEY_TO_RU = {_k: _it["canonical_ru"] for _k, _it in CATALOG.items()}


def key_to_name(key): return None if not key else _KEY_TO_RU.get(key, key)
def name_to_key(name): return None if not name else _NAME_TO_KEY.get(name.strip().lower())
def _to_f(v): return float(v) if v is not None else None
def _canon_static(name: str) -> str:
    """Deterministic catalog snap (no AI): any known name/alias → canonical_ru,
    else the input unchanged. The single name-canonicalization rule (DB-5)."""
    k = name_to_key(name)
    return key_to_name(k) if k else (name or "")


def exercise_type(name: str, key: str | None = None) -> str:
    n = (name or "").lower()
    mg = (CATALOG.get(key or "") or {}).get("muscle_group")
    if mg == "cardio" or "планк" in n or "велосипед" in n:
        return "time"
    if any(w in n for w in ("подтяг", "отжим", "брус")):
        return "bodyweight"
    return "strength"


# ─────────────────────────────── app ────────────────────────────────────────

app = FastAPI(title="Fitness prototype API")
app.add_middleware(
    CORSMiddleware, allow_origins=[WEB_ORIGIN], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def session_mw(request: Request, call_next):
    path = request.url.path
    request.state.uid = None
    if path.startswith("/api/") and path not in PUBLIC:
        uid = auth.parse_session(request.cookies.get(COOKIE))
        from_cookie = uid is not None
        if not uid and not IS_PROD:
            uid = os.getenv("DEV_UID", "local")  # local dev: no Telegram needed
        if not uid:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        # Live access re-check: an admin's block/revoke must end an active session,
        # not merely block the next login. Dev-fallback uid skips this (no local gate).
        if from_cookie and not await _is_active(uid):
            return JSONResponse({"detail": "access revoked"}, status_code=401)
        request.state.uid = uid
    return await call_next(request)


# Security headers. CSP is permissive for inline handlers/styles (the app uses
# them throughout) and the Telegram Login Widget (script from telegram.org, OAuth
# iframe from oauth.telegram.org); it still locks down object/base/frame-ancestors
# and restricts external script/connect origins. esc() (phase 1.2) is the primary
# XSS defense; this is defence-in-depth.
#
# 'unsafe-eval' is required by telegram-widget.js: it parses the data-onauth
# attribute via __parseFunction(), which calls eval() during widget init. Without
# it the browser blocks that eval, the init aborts, and the OAuth iframe is never
# created (the "login button doesn't render" bug from phase 5.3). The iframe src is
# a plain https://oauth.telegram.org URL, so frame-src alone covers it — no blob:
# or worker-src needed.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://telegram.org; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://raw.githubusercontent.com; "
    "connect-src 'self'; "
    "frame-src https://oauth.telegram.org https://telegram.org; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # Always revalidate the unhashed static shell (index.html/app.js/sw.js/styles)
    # so a deploy is never masked by a stale HTTP/heuristic cache. ETag still gives
    # cheap 304s when unchanged; this kills the stale-shell class of bug.
    if not request.url.path.startswith("/api/") and request.url.path != "/healthz":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def current_uid(request: Request) -> str:
    uid = getattr(request.state, "uid", None)
    if not uid:
        raise HTTPException(401, "unauthorized")
    return uid


# ─────────────────────── ownership guards (IDOR) ────────────────────────────
# Every {id} access is scoped to its owner; a mismatch (or missing) returns 404
# so we never leak the existence of another user's objects.

async def _own_workout(uid: str, wid: int) -> None:
    if await db.workout_owner(wid) != uid:
        raise HTTPException(404, "not found")


async def _own_plan(uid: str, pid: int) -> None:
    if await db.planned_workout_owner(pid) != uid:
        raise HTTPException(404, "not found")


async def _own_set(uid: str, sid: int) -> None:
    if await db.set_owner(sid) != uid:
        raise HTTPException(404, "not found")


async def _own_measurement(uid: str, mid: int) -> None:
    if await db.measurement_owner(mid) != uid:
        raise HTTPException(404, "not found")


async def today_for(uid: str) -> date:
    """User-local 'today' (mirrors the bot's tz.today), not the container's UTC
    date — so 'today'/'today's plan' are correct for users outside UTC."""
    return await tz.today(uid)


# ───────────────────────────── AI rate limit ────────────────────────────────

async def check_and_bump_ai(uid: str, limit: int | None = None) -> int:
    """Atomically count one AI call for the user's (local) day. Raises 429 once
    the daily limit is exceeded. Use before any guaranteed AI call."""
    limit = AI_DAILY_LIMIT if limit is None else limit
    day = await today_for(uid)
    async with get_session() as s:
        r = await s.execute(
            text("""
                INSERT INTO ai_usage (uid, day, count) VALUES (:u, :d, 1)
                ON CONFLICT (uid, day) DO UPDATE SET count = ai_usage.count + 1
                RETURNING count
            """),
            {"u": uid, "d": day},
        )
        n = r.scalar_one()
    if n > limit:
        raise HTTPException(429, "дневной лимит AI исчерпан")
    return n


async def _ai_over_limit(uid: str, limit: int | None = None) -> bool:
    """Read-only check (no bump) — for soft-gating opportunistic AI work that
    must not 429 a save (e.g. exercise-name normalization)."""
    limit = AI_DAILY_LIMIT if limit is None else limit
    day = await today_for(uid)
    n = await _scalar("SELECT count FROM ai_usage WHERE uid = :u AND day = :d", u=uid, d=day)
    return (n or 0) >= limit


# ───────────────────────────── access control ───────────────────────────────
# Gate: every login is checked against app_access. Owner (OWNER_TELEGRAM_USER_ID)
# and ALLOWED_TELEGRAM_USER_IDS are auto-approved; everyone else starts 'pending'
# until an admin approves them.

def is_owner(uid: Optional[str]) -> bool:
    return bool(OWNER_UID) and str(uid) == OWNER_UID


async def get_access(uid: str) -> Optional[dict]:
    rows = await _rows("SELECT * FROM app_access WHERE uid = :u", u=uid)
    return rows[0] if rows else None


async def is_admin(uid: str) -> bool:
    if is_owner(uid):
        return True
    role = await _scalar(
        "SELECT role FROM app_access WHERE uid = :u AND status = 'approved'", u=uid)
    return role == "admin"


async def current_admin(uid: str = Depends(current_uid)) -> str:
    if not await is_admin(uid):
        raise HTTPException(403, "только для администраторов")
    return uid


async def _is_active(uid: str) -> bool:
    """Whether uid may currently use the app — checked live on each request so a
    block/revoke ends an active session, not just the next login."""
    if is_owner(uid):
        return True
    st = await _scalar("SELECT status FROM app_access WHERE uid = :u", u=uid)
    return st == "approved"


# Transaction-level lock key serializing admin-set mutations (last-admin guard).
_ADMIN_LOCK = 78146565


def _display_from(data: Optional[dict]) -> Optional[str]:
    if not data:
        return None
    name = ((data.get("first_name") or "").strip() + " "
            + (data.get("last_name") or "").strip()).strip()
    return name or None


async def _upsert_access(uid: str, *, status: str, role: str,
                         data: Optional[dict] = None, decided: bool = False,
                         display_name: Optional[str] = None,
                         invited_by: Optional[str] = None) -> None:
    if display_name is None:
        display_name = _display_from(data)
    username = (data.get("username") if data else None) or None
    async with get_session() as s:
        await s.execute(
            text("""
                INSERT INTO app_access (uid, status, role, display_name, username,
                                        invited_by, decided_at)
                VALUES (:uid, :status, :role, :dn, :un, :inv,
                        CASE WHEN :dec THEN now() ELSE NULL END)
                ON CONFLICT (uid) DO UPDATE SET
                  status      = EXCLUDED.status,
                  role        = EXCLUDED.role,
                  display_name = COALESCE(EXCLUDED.display_name, app_access.display_name),
                  username    = COALESCE(EXCLUDED.username, app_access.username),
                  invited_by  = COALESCE(app_access.invited_by, EXCLUDED.invited_by),
                  decided_at  = CASE WHEN :dec THEN now() ELSE app_access.decided_at END
            """),
            {"uid": uid, "status": status, "role": role, "dn": display_name,
             "un": username, "inv": invited_by, "dec": decided},
        )


async def gate_login(uid: str, data: dict) -> str:
    """Decide a login: returns 'approved' | 'pending' | 'blocked'.
    First-time users are recorded as 'pending'."""
    if is_owner(uid):
        return "approved"
    row = await get_access(uid)
    # An explicit admin block wins over everything (including the env allowlist),
    # so a blocked user cannot re-enter by being on ALLOWED_TELEGRAM_USER_IDS.
    if row and row["status"] == "blocked":
        return "blocked"
    if uid in auth.ALLOWED:  # explicit env allowlist → auto-approve + persist
        if not row or row["status"] != "approved":
            await _upsert_access(uid, status="approved",
                                 role=(row or {}).get("role") or "user",
                                 data=data, decided=True)
        return "approved"
    if row is None:
        await _upsert_access(uid, status="pending", role="user", data=data, decided=False)
        return "pending"
    return row["status"] if row["status"] in ("approved", "blocked", "pending") else "pending"


def _access_public(row: dict, viewer_uid: str) -> dict:
    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    return {
        "uid": row["uid"], "status": row["status"], "role": row["role"],
        "display_name": row.get("display_name"), "username": row.get("username"),
        "invited_by": row.get("invited_by"),
        "requested_at": _iso(row.get("requested_at")),
        "decided_at": _iso(row.get("decided_at")),
        "is_owner": is_owner(row["uid"]), "is_self": row["uid"] == viewer_uid,
    }


def _load_rename_map() -> dict:
    """DB-4/6: legacy exercise name → canonical v2 name (data/legacy_rename_map.json)."""
    p = os.path.join(os.path.dirname(__file__), "..", "data", "legacy_rename_map.json")
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        return {str(k): str(v) for k, v in (m or {}).items() if k and v and str(k) != str(v)}
    except Exception:
        return {}


async def _migrate_legacy_names() -> None:
    """DB-4/6: rename legacy exercise names to their canonical v2 form across ALL
    stored data — exercise_sets.exercise_name, exercise_aliases.canonical, and the
    JSONB name fields inside planned_workouts.exercises[] and routines.days[].exercises[].
    Canonical names are shared (not per-user), so this is global. Idempotent: every
    UPDATE is keyed on the old name, so a re-run finds nothing. Guarded by a marker
    keyed on the map's content hash, so it runs once per map version (and re-runs
    automatically if the map grows). Logs before/after affected counts. The whole
    thing is one transaction; on failure it rolls back (marker not kept) and retries
    next boot. A failure never crashes startup."""
    import hashlib
    rename = _load_rename_map()
    if not rename:
        print("[migration] legacy_rename: no map present, skipped", flush=True)
        return
    digest = hashlib.sha256(json.dumps(rename, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    marker = f"legacy_rename:{digest}"
    olds = list(rename.keys())
    async with get_session() as s:
        await s.execute(text(
            "CREATE TABLE IF NOT EXISTS data_migrations ("
            "key TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"))
        # cheap verification, logged every boot: how many sets still carry a legacy name
        rem = (await s.execute(text("SELECT count(*) FROM exercise_sets WHERE exercise_name = ANY(:o)"), {"o": olds})).scalar() or 0
        if (await s.execute(text("SELECT 1 FROM data_migrations WHERE key = :k"), {"k": marker})).scalar():
            print(f"[migration] legacy_rename {marker} already applied; remaining legacy sets={rem}", flush=True)
            return  # already applied for this exact map
        counts = {"exercise_sets": 0, "exercise_aliases": 0, "planned_workouts": 0, "routines": 0}
        for old, new in rename.items():
            r = await s.execute(text("UPDATE exercise_sets SET exercise_name = :new WHERE exercise_name = :old"),
                                {"new": new, "old": old})
            counts["exercise_sets"] += r.rowcount or 0
            r = await s.execute(text("UPDATE exercise_aliases SET canonical = :new WHERE canonical = :old"),
                                {"new": new, "old": old})
            counts["exercise_aliases"] += r.rowcount or 0
        # planned_workouts.exercises JSONB: [{name, ...}]
        for row in (await s.execute(text("SELECT id, exercises FROM planned_workouts"))).mappings().all():
            exs = row["exercises"]
            if isinstance(exs, str):
                exs = json.loads(exs or "[]")
            if not isinstance(exs, list):
                continue
            changed = False
            for e in exs:
                if isinstance(e, dict) and e.get("name") in rename:
                    e["name"] = rename[e["name"]]; changed = True
            if changed:
                await s.execute(text("UPDATE planned_workouts SET exercises = CAST(:exs AS jsonb) WHERE id = :id"),
                                {"exs": json.dumps(exs, ensure_ascii=False), "id": row["id"]})
                counts["planned_workouts"] += 1
        # routines.days JSONB: [{exercises:[{name, ...}]}]
        for row in (await s.execute(text("SELECT id, days FROM routines"))).mappings().all():
            days = row["days"]
            if isinstance(days, str):
                days = json.loads(days or "[]")
            if not isinstance(days, list):
                continue
            changed = False
            for d in days:
                for e in (d.get("exercises") or []) if isinstance(d, dict) else []:
                    if isinstance(e, dict) and e.get("name") in rename:
                        e["name"] = rename[e["name"]]; changed = True
            if changed:
                await s.execute(text("UPDATE routines SET days = CAST(:days AS jsonb) WHERE id = :id"),
                                {"days": json.dumps(days, ensure_ascii=False), "id": row["id"]})
                counts["routines"] += 1
        await s.execute(text("INSERT INTO data_migrations (key) VALUES (:k) ON CONFLICT DO NOTHING"), {"k": marker})
        rem_after = (await s.execute(text("SELECT count(*) FROM exercise_sets WHERE exercise_name = ANY(:o)"), {"o": olds})).scalar() or 0
        msg = f"[migration] legacy_rename {marker} APPLIED map_size={len(rename)} affected={counts} legacy_sets_before={rem} after={rem_after}"
        print(msg, flush=True)
        log.info(msg)


@app.on_event("startup")
async def _startup() -> None:
    log.info("API startup: env=%s release=%s sentry=%s", APP_ENV, GIT_SHA or "unknown", bool(SENTRY_DSN))
    async with engine.begin() as conn:
        for stmt in (s.strip() for s in CREATE_SQL.split(";") if s.strip()):
            await conn.execute(text(stmt))
        if OWNER_UID:  # the owner is always an approved admin and cannot be locked out
            await conn.execute(
                text("""
                    INSERT INTO app_access (uid, status, role, decided_at)
                    VALUES (:u, 'approved', 'admin', now())
                    ON CONFLICT (uid) DO UPDATE
                      SET status = 'approved', role = 'admin'
                """),
                {"u": OWNER_UID},
            )
    try:
        await _migrate_legacy_names()  # DB-4/6: canonicalize legacy names in existing data
    except Exception:
        log.exception("legacy_rename migration failed (non-fatal, will retry next boot)")
    if not IS_PROD:  # local dev convenience: seed demo data for the dev user
        dev = os.getenv("DEV_UID", "local")
        have = await _scalar("SELECT COUNT(*) FROM workouts WHERE user_id=:u", u=dev) or 0
        plans = await _scalar("SELECT COUNT(*) FROM planned_workouts WHERE user_id=:u", u=dev) or 0
        if have == 0 and plans == 0:
            await _seed(dev)
    asyncio.create_task(_startup_ai_probe())  # non-blocking: a dead key pages us via log→Sentry


async def _startup_ai_probe() -> None:
    """One light AI ping at boot so an invalid key surfaces immediately (in the
    log, which Sentry's logging integration turns into an event) instead of on the
    next user. Also warms the /healthz/ai cache."""
    try:
        h = await _ai_health_check()
        _ai_health["data"] = h; _ai_health["at"] = time.time()
        if h.get("anthropic") != "ok" or h.get("openai") != "ok":
            log.error("AI key health at startup: %s", h)
        else:
            log.info("AI keys OK at startup")
    except Exception as e:
        log.error("AI startup probe failed: %s", e)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await engine.dispose()


# ──────────────────────────── helpers (custom SQL) ──────────────────────────

async def _scalar(sql: str, **p):
    async with get_session() as s:
        r = await s.execute(text(sql), p)
        return r.scalar()


async def _rows(sql: str, **p) -> list[dict]:
    async with get_session() as s:
        r = await s.execute(text(sql), p)
        return [dict(x) for x in r.mappings().all()]


async def last_result(uid: str, exercise_name: str) -> dict | None:
    rows = await _rows(
        """
        SELECT es.weight_kg, es.reps, es.duration_seconds, w.workout_date
        FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
        WHERE w.user_id = :u AND w.finished_at IS NOT NULL
          AND lower(es.exercise_name) = lower(:n) AND es.is_warmup = false
        ORDER BY w.workout_date DESC, es.weight_kg DESC NULLS LAST, es.reps DESC NULLS LAST
        LIMIT 1
        """,
        u=uid, n=exercise_name,
    )
    if not rows:
        return None
    r = rows[0]
    return {"weight_kg": _to_f(r["weight_kg"]), "reps": r["reps"],
            "duration_seconds": r["duration_seconds"], "date": r["workout_date"]}


def suggestion_from_plan(pe: dict) -> dict:
    reps = pe.get("target_reps_min") or pe.get("target_reps_max") or pe.get("target_reps")
    return {"weight_kg": _to_f(pe.get("target_weight")), "reps": reps,
            "duration_seconds": pe.get("target_duration_seconds")}


# ───────────────────────────── auth / config ────────────────────────────────

@app.get("/healthz")
async def healthz():
    ok = await _scalar("SELECT 1")
    return {"ok": ok == 1, "env": APP_ENV}


# ── AI key health (phase 4) — make a dead key LOUD, not silent ───────────────
_AI_HEALTH_TTL = 60.0
_ai_health: dict = {"at": 0.0, "data": None}


def _safe_err(e: Exception) -> str:
    """Provider error text minus anything key-shaped, so /healthz/ai never leaks
    secret material even when a misconfigured key is echoed back by the provider."""
    import re
    s = re.sub(r"sk-[A-Za-z0-9_\-]{3,}", "sk-***", str(e))
    return "err: " + s[:160]


async def _ai_health_check() -> dict:
    """Cheap real ping of each provider (1-token message / models.list)."""
    out: dict = {}
    try:
        from app.bot.services.ai_parser import PARSER_MODEL, _anthropic
        await _anthropic.messages.create(model=PARSER_MODEL, max_tokens=1,
                                          messages=[{"role": "user", "content": "ping"}])
        out["anthropic"] = "ok"
    except Exception as e:
        out["anthropic"] = _safe_err(e)
    try:
        from app.bot.services.ai_parser import _openai
        await _openai.models.list()
        out["openai"] = "ok"
    except Exception as e:
        out["openai"] = _safe_err(e)
    return out


@app.get("/healthz/ai")
async def healthz_ai():
    """Public so an external uptime monitor can watch it. Cached for 60s so it
    can't be used to burn AI quota by hammering. 200 if both providers ok, else 503."""
    now = time.time()
    if not _ai_health["data"] or now - _ai_health["at"] > _AI_HEALTH_TTL:
        _ai_health["data"] = await _ai_health_check()
        _ai_health["at"] = now
    data = _ai_health["data"]
    ok = data.get("anthropic") == "ok" and data.get("openai") == "ok"
    return JSONResponse(data, status_code=200 if ok else 503)


@app.get("/api/config")
async def config():
    return {"bot_username": BOT_USERNAME}


@app.post("/api/auth/telegram")
async def auth_telegram(request: Request):
    data = await request.json()
    if not auth.verify_telegram_login(data):
        raise HTTPException(401, "подпись не прошла проверку")
    uid = str(data.get("id"))
    decision = await gate_login(uid, data)
    if decision != "approved":
        # 403 with the gate status so the client can show the right screen.
        return JSONResponse({"status": decision}, status_code=403)
    resp = JSONResponse({"ok": True, "user_id": uid})
    resp.set_cookie(COOKIE, auth.make_session(uid), max_age=auth.SESSION_TTL,
                    httponly=True, secure=IS_PROD, samesite="lax", path="/")
    return resp


@app.get("/api/auth/me")
async def auth_me(uid: str = Depends(current_uid)):
    return {"user_id": uid}


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ────────────────────────── service (all users) ─────────────────────────────
# Thin wrappers over the bot's maintenance helpers (stats / timezone / wipes).

@app.get("/api/service/stats")
async def service_stats(uid: str = Depends(current_uid)):
    return await db.db_stats(uid)


@app.get("/api/service/tz")
async def service_tz_get(uid: str = Depends(current_uid)):
    return {"tz": await tz.user_tz_name(uid)}


class TzBody(BaseModel):
    tz: str


@app.post("/api/service/tz")
async def service_tz_set(body: TzBody, uid: str = Depends(current_uid)):
    name = (body.tz or "").strip()
    try:
        ZoneInfo(name)
    except Exception:
        raise HTTPException(422, "неизвестный часовой пояс")
    await tz.set_user_tz(uid, name)
    return {"tz": await tz.user_tz_name(uid)}


class TzCoords(BaseModel):
    lat: float
    lon: float


@app.post("/api/service/tz/coords")
async def service_tz_coords(body: TzCoords, uid: str = Depends(current_uid)):
    # timezonefinder is sync + CPU-heavy → keep it off the event loop.
    name = await asyncio.to_thread(tz.tz_from_coords, body.lat, body.lon)
    if not name:
        raise HTTPException(422, "не удалось определить пояс по координатам")
    await tz.set_user_tz(uid, name)
    return {"tz": name}


# what -> (callable, takes_uid). All per-user. The global alias cache is wiped
# via the admin-only endpoint below (it affects every user, not just the caller).
_WIPE = {
    "plans": (db.wipe_planned_workouts, True),
    "history": (db.wipe_workouts, True),
    "measurements": (db.wipe_measurements, True),
    "photos": (db.wipe_photos, True),
    "all": (db.wipe_all_user_data, True),
}


@app.post("/api/service/wipe/{what}")
async def service_wipe(what: str, uid: str = Depends(current_uid)):
    entry = _WIPE.get(what)
    if entry is None:
        raise HTTPException(404, "неизвестная цель очистки")
    fn, takes_uid = entry
    deleted = await (fn(uid) if takes_uid else fn())
    return {"deleted": deleted}


# ───────────────────────── access management (admin) ────────────────────────

@app.get("/api/admin/users")
async def admin_users(uid: str = Depends(current_admin)):
    rows = await _rows("""
        SELECT uid, status, role, display_name, username, invited_by,
               requested_at, decided_at
        FROM app_access
        ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                 requested_at ASC
    """)
    return [_access_public(r, uid) for r in rows]


class AdminUserCreate(BaseModel):
    uid: str
    display_name: Optional[str] = None


@app.post("/api/admin/users")
async def admin_user_add(body: AdminUserCreate, admin_uid: str = Depends(current_admin)):
    new_uid = (body.uid or "").strip()
    if not new_uid:
        raise HTTPException(422, "нужен Telegram-ID")
    if not new_uid.isdigit():
        raise HTTPException(422, "Telegram-ID должен быть числом")
    if is_owner(new_uid):
        raise HTTPException(409, "это владелец — уже администратор")
    existing = await get_access(new_uid)
    role = "admin" if (existing and existing["role"] == "admin") else "user"
    await _upsert_access(
        new_uid, status="approved", role=role, decided=True,
        display_name=(body.display_name or "").strip() or None, invited_by=admin_uid)
    return _access_public(await get_access(new_uid), admin_uid)


class AdminUserPatch(BaseModel):
    status: Optional[str] = None
    role: Optional[str] = None


@app.patch("/api/admin/users/{target}")
async def admin_user_patch(target: str, body: AdminUserPatch,
                           admin_uid: str = Depends(current_admin)):
    target = (target or "").strip()
    if is_owner(target):
        raise HTTPException(409, "владельца нельзя изменить")
    row = await get_access(target)
    if not row:
        raise HTTPException(404, "пользователь не найден")
    if body.status is not None and body.status not in ("pending", "approved", "blocked"):
        raise HTTPException(422, "недопустимый статус")
    if body.role is not None and body.role not in ("user", "admin"):
        raise HTTPException(422, "недопустимая роль")
    if body.status is None and body.role is None:
        raise HTTPException(422, "нечего менять")
    eff_status = body.status if body.status is not None else row["status"]
    eff_role = body.role if body.role is not None else row["role"]
    # Promotion to admin implies approval; a non-approved user holds no role
    # (so blocking an admin actually revokes admin, and states stay consistent).
    if body.role == "admin" and eff_status != "approved":
        eff_status = "approved"
    if eff_status != "approved":
        eff_role = "user"
    # Never strand the system without an admin (covers self-demote / self-block).
    was_admin = row["status"] == "approved" and row["role"] == "admin"
    will_admin = eff_status == "approved" and eff_role == "admin"
    async with get_session() as s:
        # Serialize admin-set mutations: count + update in one locked transaction
        # so two concurrent demotions can't both pass the guard and strand 0 admins.
        await s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADMIN_LOCK})
        if was_admin and not will_admin:
            n = (await s.execute(text(
                "SELECT COUNT(*) FROM app_access WHERE status='approved' AND role='admin'"
            ))).scalar() or 0
            if n <= 1:
                raise HTTPException(409, "нельзя снять последнего администратора")
        await s.execute(
            text("UPDATE app_access SET status = :st, role = :rl, decided_at = now() "
                 "WHERE uid = :u"),
            {"st": eff_status, "rl": eff_role, "u": target})
    return _access_public(await get_access(target), admin_uid)


@app.post("/api/admin/wipe-aliases")
async def admin_wipe_aliases(admin_uid: str = Depends(current_admin)):
    """The AI exercise-alias cache is global (shared by all users), so only an
    admin may clear it."""
    n = await db.wipe_exercise_aliases()
    return {"deleted": n}


# ────────────────────────────── dashboard ───────────────────────────────────

async def _week_streak(uid: str, today: date) -> int:
    """Consecutive ISO weeks (ending at the current or most-recent week) with at
    least one finished workout. Forgiving: an empty current week doesn't break a
    streak that's still alive from last week."""
    rows = await _rows(
        """SELECT DISTINCT date_trunc('week', workout_date)::date AS wk
           FROM workouts WHERE user_id=:u AND finished_at IS NOT NULL
           ORDER BY wk DESC LIMIT 104""", u=uid)
    weeks = {r["wk"] for r in rows}
    monday = today - timedelta(days=today.weekday())
    cur = monday if monday in weeks else monday - timedelta(days=7)
    streak = 0
    while cur in weeks:
        streak += 1
        cur -= timedelta(days=7)
    return streak


@app.get("/api/dashboard")
async def dashboard(uid: str = Depends(current_uid)):
    today = await today_for(uid)
    monday = today - timedelta(days=today.weekday())  # UX-5: current calendar week (Mon–Sun), user-tz
    # Phase 5: all dashboard reads are independent once `today` is known — run them
    # in one parallel round-trip instead of ~11 serial awaits (each its own session;
    # the pool covers the concurrency).
    (plans, active, last_m, week_count, last_workout, week_tonnage,
     wt_rows, pr_rows, upcoming, srow, streak) = await asyncio.gather(
        db.get_planned_workouts_range(uid, today, today),
        db.get_active_workout(uid),
        db.get_last_measurement(uid),
        _scalar("SELECT COUNT(*) FROM workouts WHERE user_id=:u AND finished_at IS NOT NULL AND workout_date >= :d",
                u=uid, d=monday),
        db.get_last_workout(uid),
        _scalar("""SELECT COALESCE(SUM(es.weight_kg * es.reps), 0)
           FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
           WHERE w.user_id=:u AND w.finished_at IS NOT NULL AND es.is_warmup=false
             AND es.weight_kg IS NOT NULL AND es.reps IS NOT NULL AND w.workout_date >= :d""",
                u=uid, d=monday),
        _rows("""SELECT taken_on, weight_kg FROM body_measurements
           WHERE user_id=:u AND weight_kg IS NOT NULL ORDER BY taken_on DESC LIMIT 8""", u=uid),
        _rows("""WITH working AS (
             SELECT es.exercise_name AS name, w.workout_date AS d, es.weight_kg AS weight,
                    MAX(es.weight_kg) OVER (
                      PARTITION BY lower(es.exercise_name) ORDER BY w.workout_date, es.set_number
                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_max
             FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
             WHERE w.user_id=:u AND w.finished_at IS NOT NULL AND es.is_warmup=false
               AND es.weight_kg IS NOT NULL AND es.reps IS NOT NULL)
           SELECT name, d, weight FROM working
           WHERE prev_max IS NOT NULL AND weight > prev_max AND d >= :since
           ORDER BY d DESC, weight DESC LIMIT 3""", u=uid, since=today - timedelta(days=30)),
        db.get_planned_workouts_range(uid, today + timedelta(days=1), today + timedelta(days=21)),
        _rows("SELECT target_weight, weekly_goal, unit FROM user_settings WHERE user_id=:u", u=uid),
        _week_streak(uid, today),
    )
    weight_trend = [{"date": r["taken_on"].isoformat(), "weight_kg": _to_f(r["weight_kg"])}
                    for r in reversed(wt_rows)]
    recent_prs = [{"name": r["name"], "date": r["d"].isoformat(), "weight_kg": _to_f(r["weight"])}
                  for r in pr_rows]
    next_plan = upcoming[0] if upcoming else None
    sg = srow[0] if srow else {}
    return {"today_plan": plans[0] if plans else None, "active_workout": active,
            "last_measurement": last_m, "week_workouts": week_count or 0,
            "last_workout": last_workout, "week_tonnage": round(_to_f(week_tonnage) or 0, 1),
            "streak": streak, "weight_trend": weight_trend,
            "recent_prs": recent_prs, "next_plan": next_plan,
            "weekly_goal": sg.get("weekly_goal"), "target_weight": _to_f(sg.get("target_weight")),
            "unit": sg.get("unit") or "kg"}


class SettingsPatch(BaseModel):
    target_weight: Optional[float] = None
    weekly_goal: Optional[int] = None
    unit: Optional[str] = None
    rest_timer_enabled: Optional[bool] = None
    rest_timer_seconds: Optional[int] = None
    recovery_mode: Optional[str] = None
    date_format: Optional[str] = None
    clear_target: bool = False


@app.get("/api/settings")
async def get_settings(uid: str = Depends(current_uid)):
    rows = await _rows(
        "SELECT tz_name, target_weight, weekly_goal, unit, rest_timer_enabled, rest_timer_seconds, recovery_mode, date_format "
        "FROM user_settings WHERE user_id=:u", u=uid)
    r = rows[0] if rows else {}
    return {"tz_name": r.get("tz_name") or "UTC", "target_weight": _to_f(r.get("target_weight")),
            "weekly_goal": r.get("weekly_goal"), "unit": r.get("unit") or "kg",
            "rest_timer_enabled": r.get("rest_timer_enabled") if r.get("rest_timer_enabled") is not None else True,
            "rest_timer_seconds": r.get("rest_timer_seconds") if r.get("rest_timer_seconds") is not None else 90,
            "recovery_mode": r.get("recovery_mode") or "natural",
            "date_format": r.get("date_format") or "DMY"}


@app.patch("/api/settings")
async def patch_settings(body: SettingsPatch, uid: str = Depends(current_uid)):
    sets: list[str] = []
    params: dict = {"u": uid}
    if body.clear_target:
        sets.append("target_weight = NULL")
    elif body.target_weight is not None:
        sets.append("target_weight = :tw"); params["tw"] = _opt_float(body.target_weight, "target_weight", 0, 1000)
    if body.weekly_goal is not None:
        sets.append("weekly_goal = :wg"); params["wg"] = _opt_int(body.weekly_goal, "weekly_goal", 0, 14)
    if body.unit is not None:
        sets.append("unit = :un"); params["un"] = body.unit if body.unit in ("kg", "lb") else "kg"
    if body.rest_timer_enabled is not None:
        sets.append("rest_timer_enabled = :rte"); params["rte"] = bool(body.rest_timer_enabled)
    if body.rest_timer_seconds is not None:
        sets.append("rest_timer_seconds = :rts"); params["rts"] = _opt_int(body.rest_timer_seconds, "rest_timer_seconds", 5, 600)
    if body.recovery_mode is not None:
        sets.append("recovery_mode = :rm"); params["rm"] = body.recovery_mode if body.recovery_mode in ("natural", "enhanced") else "natural"
    if body.date_format is not None:
        sets.append("date_format = :df"); params["df"] = body.date_format if body.date_format in ("DMY", "YMD", "MDY") else "DMY"
    if not sets:
        return {"ok": True}
    async with get_session() as s:
        await s.execute(text("INSERT INTO user_settings (user_id) VALUES (:u) ON CONFLICT (user_id) DO NOTHING"), {"u": uid})
        await s.execute(text(f"UPDATE user_settings SET {', '.join(sets)}, updated_at = now() WHERE user_id = :u"), params)
    return {"ok": True}


# ───────────────────── AI coach: "build next week" (flagship) ────────────────

class CoachContext(BaseModel):
    answers: Optional[dict] = None
    recovery_mode: Optional[str] = None


class GenerateWeek(BaseModel):
    from_date: Optional[str] = None
    weeks: int = 1
    answers: Optional[dict] = None


class ApplyWeek(BaseModel):
    days: list[dict]
    mode: Optional[str] = None   # UX2-4: None→ask on conflict | 'replace' | 'add'


@app.get("/api/coach/context")
async def coach_context_get(uid: str = Depends(current_uid)):
    crow = await _rows("SELECT data FROM coach_context WHERE uid=:u", u=uid)
    srow = await _rows("SELECT recovery_mode FROM user_settings WHERE user_id=:u", u=uid)
    return {"answers": (crow[0]["data"] if crow else {}) or {},
            "recovery_mode": (srow[0]["recovery_mode"] if srow else None) or "natural"}


@app.post("/api/coach/context")
async def coach_context_post(body: CoachContext, uid: str = Depends(current_uid)):
    async with get_session() as s:
        if body.answers is not None:
            await s.execute(text("""
                INSERT INTO coach_context (uid, data) VALUES (:u, CAST(:d AS jsonb))
                ON CONFLICT (uid) DO UPDATE SET data = CAST(:d AS jsonb), updated_at = now()
            """), {"u": uid, "d": json.dumps(body.answers, ensure_ascii=False)})
        if body.recovery_mode is not None:
            rm = body.recovery_mode if body.recovery_mode in ("natural", "enhanced") else "natural"
            await s.execute(text("INSERT INTO user_settings (user_id) VALUES (:u) ON CONFLICT (user_id) DO NOTHING"), {"u": uid})
            await s.execute(text("UPDATE user_settings SET recovery_mode=:rm, updated_at=now() WHERE user_id=:u"), {"u": uid, "rm": rm})
    return {"ok": True}


@app.delete("/api/coach/context")
async def coach_context_delete(uid: str = Depends(current_uid)):
    async with get_session() as s:
        await s.execute(text("DELETE FROM coach_context WHERE uid=:u"), {"u": uid})
    return {"ok": True}


@app.post("/api/coach/generate-week")
async def coach_generate_week(body: GenerateWeek, uid: str = Depends(current_uid)):
    """Deep-analysis week from the user's own DB data + recovery mode + survey.
    Returns a PREVIEW (not saved); the client confirms via /api/coach/apply."""
    await check_and_bump_ai(uid)
    try:
        from app.bot.services.week_coach import CoachError, build_week_brief, generate_week
    except Exception:
        raise HTTPException(503, "AI-наставник недоступен")
    brief = await build_week_brief(uid)
    srow = await _rows("SELECT recovery_mode FROM user_settings WHERE user_id=:u", u=uid)
    recovery_mode = (srow[0]["recovery_mode"] if srow else None) or "natural"
    answers = body.answers
    if answers is None:
        crow = await _rows("SELECT data FROM coach_context WHERE uid=:u", u=uid)
        answers = (crow[0]["data"] if crow else {}) or {}
    try:
        result = await generate_week(brief, recovery_mode, answers)
    except CoachError as e:
        raise HTTPException(502, f"ИИ-наставник недоступен: {str(e)[:200]}")
    if body.from_date:
        try:
            start = date.fromisoformat(body.from_date.strip())
        except ValueError:
            raise HTTPException(422, "bad from_date, expected YYYY-MM-DD")
    else:
        # «Собери СЛЕДУЮЩУЮ неделю»: default to next week's Monday (the UI always
        # sends it explicitly; this keeps direct API calls true to the name).
        today = await today_for(uid)
        start = today + timedelta(days=7 - today.weekday())
    monday = start - timedelta(days=start.weekday())
    out_days = []
    for d in result.get("days", []):
        wd = _opt_int(d.get("weekday"), "weekday", 0, 6) or 0
        target = monday + timedelta(days=wd)
        if target < start:
            target += timedelta(days=7)
        exs = _clean_plan_exercises(d.get("exercises") or [])
        if not exs:
            continue  # rest day — no plan row
        out_days.append({"date": target.isoformat(), "weekday": wd,
                         "focus_label": _opt_str(d.get("focus_label"), 200),
                         "notes": _opt_str(d.get("notes")), "exercises": exs})
    out_days.sort(key=lambda x: x["date"])
    return {"days": out_days, "rationale": result.get("rationale", ""),
            "flags": result.get("flags", []), "recovery_mode": recovery_mode}


@app.post("/api/coach/apply")
async def coach_apply(body: ApplyWeek, uid: str = Depends(current_uid)):
    """Save a confirmed coach week into planned_workouts (reuses the plan path)."""
    today = await today_for(uid)
    days = body.days or []
    targets = [_resolve_plan_date(d.get("date"), _opt_int(d.get("weekday"), "weekday", 0, 6), today) for d in days]
    await _guard_bulk_plan_conflict(uid, targets, body.mode)   # UX2-4
    saved = []
    for d, dt in zip(days, targets):
        exs = await _normalize_plan_exercises(_clean_plan_exercises(d.get("exercises") or []), uid)
        pid = await db.create_planned_workout(uid, dt, _opt_str(d.get("focus_label"), 200), exs)
        if d.get("notes"):
            await db.update_planned_workout_notes(pid, _opt_str(d.get("notes")))
        saved.append({"id": pid, "planned_date": dt.isoformat()})
    return {"saved": len(saved), "plans": saved}


# ────────────────────────────── workouts ────────────────────────────────────

class StartWorkout(BaseModel):
    from_plan_id: Optional[int] = None
    repeat_from: Optional[int] = None
    focus_label: Optional[str] = None


@app.post("/api/workouts")
async def start_workout(body: StartWorkout, uid: str = Depends(current_uid)):
    today = await today_for(uid)
    if body.from_plan_id:
        await _own_plan(uid, body.from_plan_id)   # 404 unless this plan belongs to uid (no cross-user read)
        plan = await db.get_planned_workout(body.from_plan_id)
        if not plan:
            raise HTTPException(404, "plan not found")
        wid = await db.create_workout(uid, today, plan.get("focus_label"), plan["id"])
        return {"id": wid}
    if body.repeat_from:
        await _own_workout(uid, body.repeat_from)  # 404 unless this workout belongs to uid
        src = await db.get_workout(body.repeat_from)
        if not src:
            raise HTTPException(404, "workout not found")
        sets = await db.get_workout_sets(body.repeat_from)
        agg: dict[str, dict] = {}
        for s in sets:
            if s["is_warmup"]:
                continue
            a = agg.setdefault(s["exercise_name"], {"name": s["exercise_name"], "target_sets": 0,
                                                    "target_weight": None, "target_reps": None})
            a["target_sets"] += 1
            if s.get("weight_kg") is not None:
                a["target_weight"] = _to_f(s["weight_kg"])
            if s.get("reps") is not None:
                a["target_reps"] = s["reps"]
        pid = await db.create_planned_workout(uid, today, src.get("focus_label"), list(agg.values()))
        wid = await db.create_workout(uid, today, src.get("focus_label"), pid)
        return {"id": wid}
    wid = await db.create_workout(uid, today, body.focus_label or "Свободная тренировка", None)
    return {"id": wid}


class ArchiveSetIn(BaseModel):
    weight_kg: Optional[float] = None
    reps: Optional[int] = None
    reps_text: Optional[str] = None
    duration_seconds: Optional[int] = None
    is_warmup: bool = False
    is_failure: bool = False


class ArchiveExerciseIn(BaseModel):
    name: str
    sets: list[ArchiveSetIn] = []


class ArchiveWorkoutIn(BaseModel):
    workout_date: str
    focus_label: Optional[str] = None
    notes: Optional[str] = None
    exercises: list[ArchiveExerciseIn] = []


@app.post("/api/workouts/archive")
async def create_archive_workout(body: ArchiveWorkoutIn, uid: str = Depends(current_uid)):
    """HIST-1: create a backdated FINISHED workout + its sets in ONE transaction.
    Exercise names are canonicalized through the unified catalog (DB-5) so the
    archive doesn't spawn duplicates. started_at/finished_at are set to the chosen
    date so the entry behaves exactly like a normally-finished workout on that day.
    Rejects future dates and empty workouts."""
    try:
        d = date.fromisoformat((body.workout_date or "").strip())
    except ValueError:
        raise HTTPException(422, "неверная дата")
    if d > await today_for(uid):
        raise HTTPException(422, "дата в будущем")
    # canonicalize names OUTSIDE the write tx (may call AI); keep the save fast/atomic
    names = [await _canonical_name(e.name, uid) for e in body.exercises]
    focus = (body.focus_label or "").strip() or "Тренировка"
    async with get_session() as s:
        wid = (await s.execute(
            text("INSERT INTO workouts (user_id, workout_date, focus_label) VALUES (:u, :d, :f) RETURNING id"),
            {"u": uid, "d": d, "f": focus})).scalar_one()
        n = 0
        for ex, nm in zip(body.exercises, names):
            if not nm:
                continue
            for st in ex.sets:
                if st.weight_kg is None and st.reps is None and st.duration_seconds is None and not st.reps_text:
                    continue
                await db.add_set_tx(s, wid, nm, weight_kg=st.weight_kg, reps=st.reps,
                                    reps_text=st.reps_text, duration_seconds=st.duration_seconds,
                                    is_warmup=st.is_warmup, is_failure=st.is_failure)
                n += 1
        if n == 0:
            raise HTTPException(422, "добавь хотя бы один подход")  # rolls back the workout insert
        await s.execute(text("UPDATE workouts SET started_at = :d, finished_at = :d, notes = :nt WHERE id = :id"),
                        {"d": d, "nt": (body.notes or "").strip() or None, "id": wid})
    return {"id": wid, "set_count": n}


@app.get("/api/workouts/active")
async def active_workout(uid: str = Depends(current_uid)):
    w = await db.get_active_workout(uid)
    if not w:
        return None
    return await assemble_workout(uid, w["id"])


@app.get("/api/workouts/week")
async def workouts_week(uid: str = Depends(current_uid)):
    today = await today_for(uid)
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    rows = await _rows(
        """
        SELECT pw.*,
          EXISTS(SELECT 1 FROM workouts w WHERE w.planned_workout_id = pw.id
                 AND w.finished_at IS NOT NULL) AS done
        FROM planned_workouts pw
        WHERE pw.user_id = :u AND pw.status = 'planned' AND pw.planned_date BETWEEN :a AND :b
        ORDER BY pw.planned_date ASC
        """, u=uid, a=monday, b=sunday)
    # UX3-FIX-3: only live ('planned') plans — soft-deleted ones (status='skipped')
    # no longer leak in. «Пропущено» is derived: a past planned day with no finished
    # workout (not the overloaded 'skipped' status that delete also uses).
    return [{"id": r["id"], "planned_date": r["planned_date"], "focus_label": r["focus_label"],
             "status": ("completed" if r["done"] else ("skipped" if r["planned_date"] < today else "planned")),
             "is_today": r["planned_date"] == today, "exercises": r["exercises"]} for r in rows]


@app.get("/api/workouts")
async def workouts_history(days: int = 30, q: Optional[str] = None, uid: str = Depends(current_uid)):
    """Finished workouts in the last `days` with set_count + tonnage, in ONE
    aggregate query (was N+1: a sets fetch per workout). Optional `q` filters by a
    substring of the focus label or any exercise name (case-insensitive, in SQL)."""
    days = max(1, min(days, 4000))   # HIST-1: archive workouts of any age must show (single-owner aggregate query is cheap)
    today = await today_for(uid)
    ql = (q or "").strip().lower()
    rows = await _rows(
        """
        SELECT w.*,
               COUNT(es.id) FILTER (WHERE es.is_warmup = false) AS set_count,
               COALESCE(ROUND(SUM(es.weight_kg * es.reps) FILTER (WHERE es.is_warmup = false))::int, 0) AS tonnage
        FROM workouts w
        LEFT JOIN exercise_sets es ON es.workout_id = w.id
        WHERE w.user_id = :u AND w.finished_at IS NOT NULL
          AND w.workout_date >= :fd AND w.workout_date <= :td
        GROUP BY w.id
        HAVING ((:q = '')
            OR lower(coalesce(w.focus_label, '')) LIKE :qp
            OR bool_or(lower(coalesce(es.exercise_name, '')) LIKE :qp))
          -- UX2-2: hide rest days (no working sets + empty/«Отдых» focus) from the journal
          AND NOT (
            COUNT(es.id) FILTER (WHERE es.is_warmup = false) = 0
            AND (w.focus_label IS NULL OR btrim(w.focus_label) = '' OR lower(w.focus_label) LIKE '%отдых%')
          )
        ORDER BY w.workout_date DESC, w.id DESC
        LIMIT 500
        """,
        u=uid, fd=today - timedelta(days=days), td=today, q=ql, qp='%' + ql + '%')
    for r in rows:
        r["set_count"] = int(r.get("set_count") or 0)
        r["tonnage"] = int(r.get("tonnage") or 0)
    return rows


@app.get("/api/workouts/{wid}")
async def workout_detail(wid: int, uid: str = Depends(current_uid)):
    await _own_workout(uid, wid)
    return await assemble_workout(uid, wid)


@app.get("/api/workouts/{wid}/template-day")
async def workout_as_template_day(wid: int, uid: str = Depends(current_uid)):
    """UX3-FEAT-1: map a finished workout to one routine day. Per exercise the
    target = (# working sets) × reps @ weight of the TOP (heaviest) working set."""
    await _own_workout(uid, wid)
    w = await db.get_workout(wid)
    if not w:
        raise HTTPException(404, "workout not found")
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for s in await db.get_workout_sets(wid):
        if s["is_warmup"]:
            continue
        nm = (s["exercise_name"] or "").strip()
        if not nm:
            continue
        if nm.lower() not in grouped:
            grouped[nm.lower()] = []
            order.append(nm)
        grouped[nm.lower()].append(s)
    exercises = []
    for nm in order:
        ws = grouped[nm.lower()]
        top = max(ws, key=lambda x: (_to_f(x["weight_kg"]) or 0))
        reps = top["reps"]
        exercises.append({"name": nm, "target_sets": len(ws),
                          "target_reps_min": reps, "target_reps_max": reps,
                          "target_weight": _to_f(top["weight_kg"]),
                          "reps_text": top["reps_text"] if reps is None else None})
    return {"weekday": w["workout_date"].weekday(), "focus_label": w.get("focus_label"), "exercises": exercises}


async def assemble_workout(uid: str, wid: int) -> dict:
    w = await db.get_workout(wid)
    if not w:
        raise HTTPException(404, "workout not found")
    sets = await db.get_workout_sets(wid)
    plan = await db.get_planned_workout(w["planned_workout_id"]) if w.get("planned_workout_id") else None
    plan_exs = (plan or {}).get("exercises") or []

    grouped: dict[str, list[dict]] = {}
    for s in sets:
        grouped.setdefault(s["exercise_name"].strip().lower(), []).append({
            "id": s["id"], "set_number": s["set_number"], "weight_kg": _to_f(s["weight_kg"]),
            "reps": s["reps"], "reps_text": s["reps_text"], "duration_seconds": s["duration_seconds"],
            "is_warmup": s["is_warmup"], "is_failure": s["is_failure"],
            "superset_group": s["superset_group"], "notes": s["notes"]})

    exercises, used = [], set()

    async def build(name: str, target: dict | None):
        keyl = name.strip().lower(); used.add(keyl)
        logged = grouped.get(keyl, [])
        key = name_to_key(name)
        working = [x for x in logged if not x["is_warmup"]]
        tgt_sets = (target or {}).get("target_sets")
        return {"name": name, "key": key, "type": exercise_type(name, key), "sets": logged,
                "target": suggestion_from_plan(target) if target else None, "target_sets": tgt_sets,
                "last": await last_result(uid, name),
                "done": bool(tgt_sets) and len(working) >= tgt_sets, "in_plan": target is not None}

    for pe in plan_exs:
        if pe.get("name"):
            exercises.append(await build(pe["name"], pe))
    for keyl in list(grouped):
        if keyl not in used:
            exercises.append(await build(_orig_name(sets, keyl), None))

    return {"id": w["id"], "focus_label": w["focus_label"], "workout_date": w["workout_date"],
            "started_at": w["started_at"], "finished_at": w["finished_at"], "notes": w["notes"],
            "planned_workout_id": w["planned_workout_id"], "exercises": exercises}


def _orig_name(sets, keyl):
    for s in sets:
        if s["exercise_name"].strip().lower() == keyl:
            return s["exercise_name"]
    return keyl


class NotesBody(BaseModel):
    notes: str


@app.patch("/api/workouts/{wid}/notes")
async def set_workout_notes(wid: int, body: NotesBody, uid: str = Depends(current_uid)):
    await _own_workout(uid, wid)
    await db.update_workout_notes(wid, body.notes)
    return {"ok": True}


@app.delete("/api/workouts/{wid}")
async def delete_workout(wid: int, uid: str = Depends(current_uid)):
    await _own_workout(uid, wid)
    async with get_session() as s:
        await s.execute(text("DELETE FROM workouts WHERE id=:id AND user_id=:u"), {"id": wid, "u": uid})
    return {"ok": True}


@app.post("/api/workouts/{wid}/finish")
async def finish_workout(wid: int, uid: str = Depends(current_uid)):
    await _own_workout(uid, wid)
    w = await db.get_workout(wid)
    if not w:
        raise HTTPException(404, "workout not found")
    await db.finish_workout(wid)
    return await workout_summary(uid, wid, w)


async def workout_summary(uid: str, wid: int, w: dict) -> dict:
    sets = await db.get_workout_sets(wid)
    working = [s for s in sets if not s["is_warmup"]]
    tonnage = round(sum((_to_f(s["weight_kg"]) or 0) * (s["reps"] or 0) for s in working))
    prev = await _rows(
        """
        SELECT id FROM workouts WHERE user_id=:u AND finished_at IS NOT NULL AND id <> :id
          AND coalesce(focus_label,'') = coalesce(:f,'')
        ORDER BY workout_date DESC, finished_at DESC LIMIT 1
        """, u=uid, id=wid, f=w.get("focus_label"))
    delta = ""
    if prev:
        pw = [s for s in await db.get_workout_sets(prev[0]["id"]) if not s["is_warmup"]]
        pton = sum((_to_f(s["weight_kg"]) or 0) * (s["reps"] or 0) for s in pw)
        if pton:
            d = round((tonnage - pton) / pton * 100)
            delta = f", {'+' if d >= 0 else ''}{d}% к прошлой такой же"
    txt = f"Тоннаж {tonnage:,} кг".replace(",", " ") + delta + "."
    if w.get("notes"):
        txt += f" Заметка: {w['notes']}"
    return {"tonnage": tonnage, "set_count": len(working), "summary": txt}


@app.get("/api/workouts/{wid}/coach")
async def workout_coach(wid: int, uid: str = Depends(current_uid)):
    """Post-workout AI coach: numeric facts (local DB) phrased by Opus.
    Degrades to a deterministic summary if Opus is unreachable; only hard
    failures (import/DB) surface as 503."""
    await _own_workout(uid, wid)
    await check_and_bump_ai(uid)
    try:
        from app.bot.services.coach import build_coach_facts, opus_coach_summary
    except Exception:
        raise HTTPException(503, "AI-коуч недоступен")
    try:
        facts = await build_coach_facts(uid, wid)
        summary = await opus_coach_summary(facts)
    except Exception as e:
        raise HTTPException(503, f"AI-разбор недоступен: {str(e)[:200]}")
    return {"summary": summary, "facts": facts}


# ──────────────────────────────── sets ──────────────────────────────────────

class AddSet(BaseModel):
    exercise_key: Optional[str] = None
    exercise_name: Optional[str] = None
    text: Optional[str] = None
    weight_kg: Optional[float] = None
    reps: Optional[int] = None
    duration_seconds: Optional[int] = None
    reps_text: Optional[str] = None
    is_warmup: bool = False
    is_failure: bool = False
    superset_group: Optional[str] = None
    sets: Optional[list[dict]] = None    # WK-2: several structured rows at once
    client_op_id: Optional[str] = None   # idempotency key for offline replay


@app.post("/api/workouts/{wid}/sets")
async def add_set(wid: int, body: AddSet, uid: str = Depends(current_uid)):
    await _own_workout(uid, wid)
    # 1) Resolve WHAT to insert (free-text parse / AI name-normalize) OUTSIDE any
    #    transaction — a slow AI call must not hold the set-write transaction open.
    rows: list[dict] = []
    if body.text:
        last = await db.get_last_set(wid)
        ctx = (last["exercise_name"] if last else None) or body.exercise_name
        parsed = parse_exercise_input(body.text, ctx)
        # The regex parser only takes the FIRST set of a comma/«и»-separated list
        # and can't read conversational/voice phrasing. Mirror the bot: fall back
        # to the AI parser when nothing parsed, or a multi-set separator hints
        # there should be several. AI runs OUTSIDE the write transaction.
        import re as _re
        if (not parsed) or (len(parsed) == 1 and _re.search(r"[,;\n]|\bи\b", body.text)):
            try:
                from app.bot.services.ai_parser import parse_set_text_ai
                ai = await parse_set_text_ai(body.text, exercise_hint=ctx)
            except Exception:
                ai = None
            if ai and len(ai) > len(parsed or []):
                from app.bot.services.set_parser import SetResult
                parsed = [SetResult(
                    exercise_name=d.get("exercise_name") or ctx or "Упражнение",
                    weight_kg=d.get("weight_kg"), reps=d.get("reps"), reps_text=d.get("reps_text"),
                    duration_seconds=d.get("duration_seconds"),
                    is_warmup=d.get("is_warmup", False), is_failure=d.get("is_failure", False),
                ) for d in ai]
        if not parsed:
            raise HTTPException(422, "не удалось разобрать ввод")
        rows = [dict(exercise_name=r.exercise_name, weight_kg=r.weight_kg, reps=r.reps,
                     reps_text=r.reps_text, duration_seconds=r.duration_seconds,
                     superset_group=r.superset_group, is_warmup=r.is_warmup,
                     is_failure=r.is_failure, notes=r.notes) for r in parsed]
    elif body.sets is not None:
        # WK-2: several structured rows at once. One name; each row carries its
        # own weight/reps/duration + warmup/failure flags. Empty rows are skipped.
        name = await _canonical_name(body.exercise_name, uid) if body.exercise_name else key_to_name(body.exercise_key)
        if not name:
            raise HTTPException(422, "нужно упражнение")

        def _num(v, cast):
            try:
                return cast(v) if v is not None and v != "" else None
            except (TypeError, ValueError):
                return None
        for r in body.sets:
            w, rp, du, rt = r.get("weight_kg"), r.get("reps"), r.get("duration_seconds"), r.get("reps_text")
            if w in (None, "") and rp in (None, "") and du in (None, "") and not rt:
                continue
            rows.append(dict(exercise_name=name, weight_kg=_num(w, float), reps=_num(rp, int),
                             reps_text=(str(rt)[:80] if rt else None), duration_seconds=_num(du, int),
                             superset_group=None, is_warmup=bool(r.get("is_warmup")), is_failure=bool(r.get("is_failure"))))
        if not rows:
            raise HTTPException(422, "нет подходов")
    else:
        name = await _canonical_name(body.exercise_name, uid) if body.exercise_name else key_to_name(body.exercise_key)
        if not name:
            raise HTTPException(422, "нужно упражнение")
        rows = [dict(exercise_name=name, weight_kg=body.weight_kg, reps=body.reps,
                     reps_text=body.reps_text, duration_seconds=body.duration_seconds,
                     superset_group=body.superset_group, is_warmup=body.is_warmup,
                     is_failure=body.is_failure)]
    # 2) ONE transaction: the idempotency marker and ALL set inserts commit
    #    atomically. If the op was already applied → duplicate, write nothing. If
    #    the request dies mid-write → the whole tx rolls back (marker NOT kept),
    #    so a replay re-applies it cleanly — no lost or doubled set.
    async with get_session() as s:
        if body.client_op_id:
            r = await s.execute(
                text("INSERT INTO processed_ops (uid, op_id) VALUES (:u, :op) "
                     "ON CONFLICT DO NOTHING RETURNING 1"),
                {"u": uid, "op": body.client_op_id})
            if r.scalar() is None:
                return {"ids": [], "duplicate": True}
        ids = [await db.add_set_tx(s, wid, **row) for row in rows]
    return {"ids": ids}


class PatchSet(BaseModel):
    weight_kg: Optional[float] = None
    reps: Optional[int] = None
    reps_text: Optional[str] = None
    duration_seconds: Optional[int] = None
    is_warmup: Optional[bool] = None
    is_failure: Optional[bool] = None
    notes: Optional[str] = None


@app.patch("/api/sets/{sid}")
async def patch_set(sid: int, body: PatchSet, uid: str = Depends(current_uid)):
    await _own_set(uid, sid)
    await db.update_set(sid, **{k: v for k, v in body.dict().items() if v is not None})
    return {"ok": True}


@app.delete("/api/sets/{sid}")
async def del_set(sid: int, uid: str = Depends(current_uid)):
    await _own_set(uid, sid)
    await db.delete_set(sid)
    return {"ok": True}


# ────────────────────────────── exercises ───────────────────────────────────

@app.get("/api/exercises/recent")
async def exercises_recent(limit: int = 12, uid: str = Depends(current_uid)):
    limit = max(1, min(limit, 200))
    rows = await _rows(
        """
        SELECT es.exercise_name AS name, MAX(es.created_at) AS last_at
        FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
        WHERE w.user_id = :u GROUP BY es.exercise_name ORDER BY last_at DESC LIMIT :lim
        """, u=uid, lim=limit)
    for r in rows:
        r["key"] = name_to_key(r["name"])
        r["image"] = (CATALOG.get(r["key"] or "") or {}).get("image")   # DB-2: thumbnail
    return rows


@app.get("/api/exercises/groups")
async def exercises_groups(uid: str = Depends(current_uid)):
    counts: dict[str, int] = {}
    for it in CATALOG.values():
        g = it.get("muscle_group") or "other"
        counts[g] = counts.get(g, 0) + 1
    # 14 groups, in the curated order, with one row per group (no duplicate labels).
    return [{"group": g, "label": GROUP_RU.get(g, g), "count": counts.get(g, 0)}
            for g in GROUP_ORDER if counts.get(g)]


@app.get("/api/exercises/catalog")
async def exercises_catalog(group: Optional[str] = None, uid: str = Depends(current_uid)):
    out = [{"exercise_key": k, "name": it["canonical_ru"], "muscle_group": it.get("muscle_group"),
            "image": it.get("image")}
           for k, it in CATALOG.items() if not group or it.get("muscle_group") == group]
    out.sort(key=lambda x: x["name"])
    return out


@app.get("/api/exercises/search")
async def exercises_search(q: str, limit: int = 8, uid: str = Depends(current_uid)):
    return _catalog_search(q, max(1, min(limit, 50)))


@app.get("/api/exercises/{key}/history")
async def exercise_history(key: str, limit: int = 10, uid: str = Depends(current_uid)):
    limit = max(1, min(limit, 500))
    name = key_to_name(key) or key
    rows = await _rows(
        """
        SELECT w.workout_date, es.weight_kg, es.reps, es.duration_seconds
        FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
        WHERE w.user_id = :u AND lower(es.exercise_name) = lower(:n)
          AND w.finished_at IS NOT NULL AND es.is_warmup = false
        ORDER BY w.workout_date DESC, es.set_number ASC LIMIT :lim
        """, u=uid, n=name, lim=limit)
    for r in rows:
        r["weight_kg"] = _to_f(r["weight_kg"])
    last = await last_result(uid, name)
    rec = {"weight_kg": last["weight_kg"], "reps": last.get("reps")} if last and last.get("weight_kg") else None
    return {"name": name, "history": rows, "recommendation": rec}


@app.get("/api/exercises/{key}/stats")
async def exercise_stats(key: str, uid: str = Depends(current_uid)):
    """Personal records + per-session progression for one exercise (owner-scoped).

    PRs over all working sets: heaviest weight, best single-set volume (weight×reps),
    and estimated 1RM (Epley: weight·(1+reps/30)). `series` is one point per session
    date (top working weight + total volume) for a progression chart. Accepts either
    an exercise key or a raw name as `{key}` (matched case-insensitively by name)."""
    name = key_to_name(key) or key
    rows = await _rows(
        """
        SELECT w.workout_date AS date, es.weight_kg AS weight, es.reps AS reps
        FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
        WHERE w.user_id = :u AND lower(es.exercise_name) = lower(:n)
          AND es.is_warmup = false
          AND es.weight_kg IS NOT NULL AND es.reps IS NOT NULL AND es.reps > 0
        ORDER BY w.workout_date ASC, es.set_number ASC
        """, u=uid, n=name)
    pr_weight = pr_vol = pr_1rm = None
    by_date: dict[str, dict] = {}
    for r in rows:
        wt = _to_f(r["weight"]) or 0.0
        reps = int(r["reps"])
        d_iso = r["date"].isoformat()
        vol = wt * reps
        epley = wt * (1 + reps / 30.0)
        if pr_weight is None or wt > pr_weight["weight"]:
            pr_weight = {"weight": wt, "reps": reps, "date": d_iso}
        if pr_vol is None or vol > pr_vol["value"]:
            pr_vol = {"value": round(vol, 1), "weight": wt, "reps": reps, "date": d_iso}
        if pr_1rm is None or epley > pr_1rm["value"]:
            pr_1rm = {"value": round(epley, 1), "weight": wt, "reps": reps, "date": d_iso}
        slot = by_date.setdefault(d_iso, {"date": d_iso, "top_weight": 0.0, "volume": 0.0})
        slot["top_weight"] = max(slot["top_weight"], wt)
        slot["volume"] += vol
    series = [
        {"date": s["date"], "top_weight": round(s["top_weight"], 1), "volume": round(s["volume"], 1)}
        for s in by_date.values()
    ]
    return {
        "name": name,
        "key": name_to_key(name),
        "sessions": len(series),
        "pr": {"weight": pr_weight, "volume": pr_vol, "one_rm": pr_1rm},
        "series": series,
    }


# ──────────────────────────────── plans ─────────────────────────────────────

_WEEKDAY_ORDER = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _resolve_plan_date(date_str: Optional[str], weekday: Optional[int], today: date) -> date:
    """Mirror the bot's date logic: explicit ISO date wins; else next occurrence
    of the given weekday (0=Mon..6=Sun), today counting as valid; else today."""
    if date_str:
        try:
            return date.fromisoformat(date_str.strip())
        except ValueError:
            raise HTTPException(422, "bad date format, expected YYYY-MM-DD")
    if weekday is not None:
        if not 0 <= weekday <= 6:
            raise HTTPException(422, "weekday must be 0..6 (Mon..Sun)")
        days_ahead = (weekday - today.weekday()) % 7
        return today + timedelta(days=days_ahead)
    return today


# UX2-4: shared duplicate-day guard for MASS plan creation (coach week, paste
# bulk, template apply). Detects target days that already hold a plan and lets
# the client choose Отменить / Заменить / Добавить.
async def _occupied_plan_dates(uid: str, dates: list[date]) -> list[str]:
    """ISO dates among `dates` that already hold a planned ('planned') workout."""
    uniq = sorted({d for d in dates})
    if not uniq:
        return []
    rows = await _rows(
        "SELECT DISTINCT planned_date FROM planned_workouts "
        "WHERE user_id=:u AND status='planned' AND planned_date = ANY(:ds)",
        u=uid, ds=uniq)
    return sorted(r["planned_date"].isoformat() for r in rows)


async def _replace_plans_on(uid: str, dates: list[date]) -> None:
    """Soft-delete (status='skipped') the user's planned workouts on these dates."""
    uniq = sorted({d for d in dates})
    if not uniq:
        return
    async with get_session() as s:
        await s.execute(text(
            "UPDATE planned_workouts SET status='skipped', updated_at=now() "
            "WHERE user_id=:u AND status='planned' AND planned_date = ANY(:ds)"),
            {"u": uid, "ds": uniq})


async def _guard_bulk_plan_conflict(uid: str, targets: list[date], mode: Optional[str]) -> None:
    """mode None → raise 409 {occupied:[...]} if any target day already has a plan;
    'replace' → soft-delete existing plans on the target days first; 'add' → no-op."""
    if mode == "add":
        return
    if mode == "replace":
        await _replace_plans_on(uid, targets)
        return
    occ = await _occupied_plan_dates(uid, targets)
    if occ:
        raise HTTPException(409, detail={"reason": "occupied", "occupied": occ})


def _opt_int(v, field: str, lo: int = 0, hi: int = 1000):
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{field}: ожидалось целое число")
    if not (lo <= n <= hi):
        raise HTTPException(422, f"{field}: вне диапазона {lo}..{hi}")
    return n


def _opt_float(v, field: str, lo: float = 0.0, hi: float = 10000.0):
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{field}: ожидалось число")
    if not (lo <= x <= hi):
        raise HTTPException(422, f"{field}: вне диапазона {lo}..{hi}")
    return x


def _opt_str(v, maxlen: int = 500):
    if v is None:
        return None
    s = str(v).strip()
    return s[:maxlen] or None


def _clean_plan_exercises(exercises: list[dict]) -> list[dict]:
    """Validate + normalize incoming exercise dicts to the bot's planned-exercise
    shape. Unknown fields are dropped; bad types/ranges -> 422. JSONB storage."""
    out: list[dict] = []
    for e in exercises or []:
        if not isinstance(e, dict):
            raise HTTPException(422, "упражнение должно быть объектом")
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        d = {
            "name": name[:200],
            "target_sets": _opt_int(e.get("target_sets"), "target_sets", 0, 100),
            "target_reps_min": _opt_int(e.get("target_reps_min"), "target_reps_min", 0, 1000),
            "target_reps_max": _opt_int(e.get("target_reps_max"), "target_reps_max", 0, 1000),
            "target_weight": _opt_float(e.get("target_weight"), "target_weight", 0, 10000),
            "reps_text": _opt_str(e.get("reps_text")),
            "notes": _opt_str(e.get("notes")),
            "superset_group": _opt_str(e.get("superset_group"), 50),
        }
        # if only one reps value provided, mirror it into min==max (bot convention)
        if d["target_reps_min"] is not None and d["target_reps_max"] is None:
            d["target_reps_max"] = d["target_reps_min"]
        # keep min <= max
        if (d["target_reps_min"] is not None and d["target_reps_max"] is not None
                and d["target_reps_min"] > d["target_reps_max"]):
            d["target_reps_min"], d["target_reps_max"] = d["target_reps_max"], d["target_reps_min"]
        out.append(d)
    return out


async def _canonical_name(raw: str, uid: str) -> str:
    """Canonicalize a free-text exercise name. FIRST snap against the unified
    catalog (catalog_v2 NAME_INDEX) deterministically — any known name/alias becomes
    its canonical_ru with NO AI, so plan-save and set-logging agree and no duplicate
    exercises are spawned (DB-5). Only genuinely unknown names fall back to the
    self-learning AI normalizer, whose result is itself re-snapped to the catalog so
    a near-alias collapses to the canon rather than a near-duplicate. Over the AI cap
    or on any failure we keep the original name — never blocks the save."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    # 1) deterministic catalog hit — instant canon, no AI
    key = name_to_key(raw)
    if key:
        return key_to_name(key)
    try:
        if await _ai_over_limit(uid):  # don't start new AI work past the daily cap
            return raw
        from app.bot.services.exercise_catalog import resolve_or_register
        res = await resolve_or_register(raw)
        if (res or {}).get("source") == "ai":
            try:
                await check_and_bump_ai(uid)  # count the actual AI call
            except HTTPException:
                pass
        canon = ((res or {}).get("canonical") or "").strip()
        if canon:
            # 2) re-snap the AI result onto the catalog (alias → canon, not near-dupe)
            k2 = name_to_key(canon)
            if k2:
                return key_to_name(k2)
            if canon.lower() != raw.lower():
                return canon
    except Exception:
        pass
    return raw


async def _normalize_plan_exercises(exs: list[dict], uid: str) -> list[dict]:
    for e in exs:
        if e.get("name"):
            e["name"] = await _canonical_name(e["name"], uid)
    return exs


class PlanExercise(BaseModel):
    name: str
    target_sets: Optional[int] = None
    target_reps_min: Optional[int] = None
    target_reps_max: Optional[int] = None
    target_weight: Optional[float] = None
    reps_text: Optional[str] = None
    notes: Optional[str] = None
    superset_group: Optional[str] = None


class CreatePlan(BaseModel):
    date: Optional[str] = None          # YYYY-MM-DD
    weekday: Optional[int] = None       # 0=Mon .. 6=Sun
    focus_label: Optional[str] = None
    notes: Optional[str] = None
    exercises: list[PlanExercise] = []


class UpdatePlan(BaseModel):
    date: Optional[str] = None
    weekday: Optional[int] = None
    focus_label: Optional[str] = None
    notes: Optional[str] = None
    exercises: Optional[list[PlanExercise]] = None


class ParsePlan(BaseModel):
    text: str


class BulkDay(BaseModel):
    date: Optional[str] = None
    weekday: Optional[int] = None
    focus_label: Optional[str] = None
    notes: Optional[str] = None
    exercises: list[PlanExercise] = []


class BulkPlans(BaseModel):
    days: list[BulkDay] = []
    mode: Optional[str] = None   # UX2-4: None→ask on conflict | 'replace' | 'add'


@app.get("/api/plans")
async def plans_list(
    days: int = 30,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    uid: str = Depends(current_uid),
):
    """Planned workouts (status='planned'): the next `days` days (default), or an
    explicit range (?from=YYYY-MM-DD&to=YYYY-MM-DD). Item shape is identical."""
    today = await today_for(uid)
    if from_ or to:
        try:
            fd = date.fromisoformat((from_ or "").strip())
            td = date.fromisoformat((to or "").strip())
        except ValueError:
            raise HTTPException(422, "bad date, expected YYYY-MM-DD")
        if fd > td:
            raise HTTPException(422, "from must be <= to")
    else:
        fd, td = today, today + timedelta(days=days)
    rows = await db.get_planned_workouts_range(uid, fd, td)
    out = []
    for p in rows:
        d = p.get("planned_date")
        out.append({
            "id": p["id"],
            "planned_date": d.isoformat() if isinstance(d, date) else d,
            "weekday": d.weekday() if isinstance(d, date) else None,
            "focus_label": p.get("focus_label"),
            "notes": p.get("notes"),
            "exercises": p.get("exercises") or [],
            "is_today": (isinstance(d, date) and d == today),
        })
    return out


@app.get("/api/plans/today")
async def plan_today(uid: str = Depends(current_uid)):
    d = await today_for(uid)
    rows = await db.get_planned_workouts_range(uid, d, d)
    return rows[0] if rows else None


@app.get("/api/plans/{pid}")
async def plan_detail(pid: int, uid: str = Depends(current_uid)):
    await _own_plan(uid, pid)
    p = await db.get_planned_workout(pid)
    if not p:
        raise HTTPException(404, "plan not found")
    d = p.get("planned_date")
    if isinstance(d, date):
        p["planned_date"] = d.isoformat()
        p["weekday"] = d.weekday()
    return p


@app.post("/api/plans")
async def create_plan(body: CreatePlan, uid: str = Depends(current_uid)):
    d = _resolve_plan_date(body.date, body.weekday, await today_for(uid))
    exs = await _normalize_plan_exercises(_clean_plan_exercises([e.model_dump() for e in body.exercises]), uid)
    pid = await db.create_planned_workout(uid, d, (body.focus_label or None), exs)
    if body.notes:
        await db.update_planned_workout_notes(pid, body.notes)
    return {"id": pid, "planned_date": d.isoformat()}


@app.patch("/api/plans/{pid}")
async def update_plan(pid: int, body: UpdatePlan, uid: str = Depends(current_uid)):
    await _own_plan(uid, pid)
    existing = await db.get_planned_workout(pid)
    if not existing:
        raise HTTPException(404, "plan not found")
    exs = None
    if body.exercises is not None:
        exs = await _normalize_plan_exercises(_clean_plan_exercises([e.model_dump() for e in body.exercises]), uid)
    await db.update_planned_workout(
        pid,
        focus_label=body.focus_label,
        exercises=exs,
    )
    if body.notes is not None:
        await db.update_planned_workout_notes(pid, body.notes)
    if body.date is not None or body.weekday is not None:
        new_date = _resolve_plan_date(body.date, body.weekday, await today_for(uid))
        async with get_session() as s:
            await s.execute(
                text("UPDATE planned_workouts SET planned_date = :d, updated_at = now() WHERE id = :id"),
                {"d": new_date, "id": pid},
            )
    return {"ok": True}


@app.delete("/api/plans/{pid}")
async def delete_plan(pid: int, uid: str = Depends(current_uid)):
    await _own_plan(uid, pid)
    await db.delete_planned_workout(pid)   # soft-delete: status='skipped' (mirrors bot)
    return {"ok": True}


@app.post("/api/plans/parse")
async def parse_plan(body: ParsePlan, uid: str = Depends(current_uid)):
    """Free-text → structured days preview (AI), with dates pre-assigned.
    Does NOT save — the client confirms via POST /api/plans/bulk."""
    if not (body.text or "").strip():
        raise HTTPException(422, "пустой текст")
    await check_and_bump_ai(uid)
    try:
        from app.bot.services.ai_parser import PlanParseError, parse_plan_text
    except Exception:
        raise HTTPException(503, "ИИ-парсер недоступен")
    try:
        days = await parse_plan_text(body.text)
    except PlanParseError as e:
        # The model call itself failed (outage/auth/bad model) — surface the cause
        # as a 502 instead of a misleading "couldn't parse" 422.
        raise HTTPException(502, f"ИИ-сервис недоступен: {str(e)[:200]}")
    if not days:
        raise HTTPException(422, "не удалось разобрать план — проверь формат")
    today = await today_for(uid)
    out = []
    for i, day in enumerate(days):
        label = (day.day_label or "").lower().strip()
        wd = next((idx for idx, nm in enumerate(_WEEKDAY_ORDER) if nm.lower() in label), None)
        if wd is not None:
            days_ahead = (wd - today.weekday()) % 7
            if days_ahead == 0 and i > 0:
                days_ahead = 7
            assigned = today + timedelta(days=days_ahead)
        else:
            try:
                assigned = date.fromisoformat((day.day_label or "").strip())
            except ValueError:
                assigned = today + timedelta(days=i)
        out.append({
            "date": assigned.isoformat(),
            "weekday": assigned.weekday(),
            "day_label": day.day_label,
            "focus_label": day.focus_label,
            "notes": day.notes,
            "exercises": [{
                "name": _canon_static(ex.name), "target_sets": ex.target_sets,
                "target_reps_min": ex.target_reps_min, "target_reps_max": ex.target_reps_max,
                "target_weight": ex.target_weight, "reps_text": ex.reps_text,
                "notes": ex.notes, "superset_group": ex.superset_group,
            } for ex in day.exercises],
        })
    return {"days": out}


@app.post("/api/plans/bulk")
async def create_plans_bulk(body: BulkPlans, uid: str = Depends(current_uid)):
    """Save multiple planned days at once (confirmed preview)."""
    today = await today_for(uid)
    targets = [_resolve_plan_date(day.date, day.weekday, today) for day in body.days]
    await _guard_bulk_plan_conflict(uid, targets, body.mode)   # UX2-4
    saved = []
    for day, d in zip(body.days, targets):
        exs = await _normalize_plan_exercises(_clean_plan_exercises([e.model_dump() for e in day.exercises]), uid)
        pid = await db.create_planned_workout(uid, d, (day.focus_label or None), exs)
        if day.notes:
            await db.update_planned_workout_notes(pid, day.notes)
        saved.append({"id": pid, "planned_date": d.isoformat()})
    return {"saved": len(saved), "plans": saved}


# ─────────────────────────── routines (templates) ───────────────────────────

class RoutineBody(BaseModel):
    name: Optional[str] = None
    days: Optional[list[dict]] = None


class ApplyRoutine(BaseModel):
    from_date: Optional[str] = None
    weeks: int = 1
    mode: Optional[str] = None   # UX2-4: None→ask on conflict | 'replace' | 'add'


def _clean_routine_days(days: list[dict]) -> list[dict]:
    """Validate routine days: each {weekday 0-6, focus_label?, exercises[]} where
    exercises use the same shape as planned_workouts (_clean_plan_exercises)."""
    out: list[dict] = []
    for d in days or []:
        if not isinstance(d, dict):
            raise HTTPException(422, "день должен быть объектом")
        wd = _opt_int(d.get("weekday"), "weekday", 0, 6)
        out.append({
            "weekday": wd if wd is not None else 0,
            "focus_label": _opt_str(d.get("focus_label"), 200),
            "exercises": _clean_plan_exercises(d.get("exercises") or []),
        })
    out.sort(key=lambda x: x["weekday"])
    return out


async def _own_routine(uid: str, rid: int) -> dict:
    rows = await _rows("SELECT id, user_id, name, days FROM routines WHERE id = :id", id=rid)
    if not rows or rows[0]["user_id"] != uid:
        raise HTTPException(404, "not found")
    return rows[0]


@app.get("/api/routines")
async def list_routines(uid: str = Depends(current_uid)):
    return await _rows(
        "SELECT id, name, days, updated_at FROM routines WHERE user_id = :u ORDER BY updated_at DESC", u=uid)


@app.post("/api/routines")
async def create_routine(body: RoutineBody, uid: str = Depends(current_uid)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(422, "укажите название шаблона")
    days = _clean_routine_days(body.days or [])
    rid = await _scalar(
        "INSERT INTO routines (user_id, name, days) VALUES (:u, :n, CAST(:d AS jsonb)) RETURNING id",
        u=uid, n=name[:200], d=json.dumps(days, ensure_ascii=False))
    return {"id": rid}


@app.get("/api/routines/{rid}")
async def get_routine(rid: int, uid: str = Depends(current_uid)):
    return await _own_routine(uid, rid)


@app.patch("/api/routines/{rid}")
async def update_routine(rid: int, body: RoutineBody, uid: str = Depends(current_uid)):
    await _own_routine(uid, rid)
    sets, params = [], {"id": rid}
    if body.name is not None:
        n = body.name.strip()
        if not n:
            raise HTTPException(422, "название не может быть пустым")
        sets.append("name = :n"); params["n"] = n[:200]
    if body.days is not None:
        sets.append("days = CAST(:d AS jsonb)")
        params["d"] = json.dumps(_clean_routine_days(body.days), ensure_ascii=False)
    if sets:
        sets.append("updated_at = now()")
        async with get_session() as s:
            await s.execute(text(f"UPDATE routines SET {', '.join(sets)} WHERE id = :id"), params)
    return {"ok": True}


@app.delete("/api/routines/{rid}")
async def delete_routine(rid: int, uid: str = Depends(current_uid)):
    await _own_routine(uid, rid)
    async with get_session() as s:
        await s.execute(text("DELETE FROM routines WHERE id = :id"), {"id": rid})
    return {"ok": True}


@app.post("/api/routines/{rid}/apply")
async def apply_routine(rid: int, body: ApplyRoutine, uid: str = Depends(current_uid)):
    """Roll the weekly template out to planned_workouts for `weeks` weeks from
    `from_date` (default today). Each routine day lands on its weekday; days before
    the start date are skipped so a mid-week start doesn't backfill the past."""
    r = await _own_routine(uid, rid)
    days = r["days"] or []
    weeks = max(1, min(int(body.weeks or 1), 12))
    if body.from_date:
        try:
            start = date.fromisoformat(body.from_date.strip())
        except ValueError:
            raise HTTPException(422, "bad from_date, expected YYYY-MM-DD")
    else:
        start = await today_for(uid)
    # Resolve every target (date, routine-day) pair first, skipping past days, so
    # the duplicate guard sees the whole set (UX2-4).
    plan_items: list[tuple[date, dict]] = []
    for wk in range(weeks):
        monday = start - timedelta(days=start.weekday()) + timedelta(days=7 * wk)
        for d in days:
            target = monday + timedelta(days=int(d.get("weekday", 0)))
            if target < start:
                continue
            plan_items.append((target, d))
    await _guard_bulk_plan_conflict(uid, [t for t, _ in plan_items], body.mode)
    created = 0
    for target, d in plan_items:
        exs = await _normalize_plan_exercises(_clean_plan_exercises(d.get("exercises") or []), uid)
        await db.create_planned_workout(uid, target, d.get("focus_label") or None, exs)
        created += 1
    return {"created": created}


# ───────────────────────────── measurements ─────────────────────────────────

class AddMeasurement(BaseModel):
    values: Optional[dict] = None
    text: Optional[str] = None
    taken_on: Optional[str] = None


@app.post("/api/measurements")
async def add_measurement(body: AddMeasurement, uid: str = Depends(current_uid)):
    values = body.values or {}
    if body.text:
        try:
            from app.bot.services.measurement_parser import parse_measurement_text
            values = {**(parse_measurement_text(body.text).get("values") or {}), **values}
        except Exception:
            raise HTTPException(503, "разбор текста замеров недоступен")
    if not values:
        raise HTTPException(422, "нет значений")
    mid = await db.create_measurement(uid, body.taken_on or (await today_for(uid)).isoformat(), values)
    return {"id": mid}


@app.get("/api/measurements")
async def list_measurements(limit: int = 30, uid: str = Depends(current_uid)):
    return await db.get_measurements(uid, max(1, min(limit, 500)))


@app.get("/api/measurements/last")
async def last_measurement(uid: str = Depends(current_uid)):
    return await db.get_last_measurement(uid)


@app.delete("/api/measurements/{mid}")
async def del_measurement(mid: int, uid: str = Depends(current_uid)):
    await _own_measurement(uid, mid)
    await db.delete_measurement(mid)
    return {"ok": True}


# ──────────────────────────────── reports ───────────────────────────────────

@app.get("/api/export")
async def export_data(format: str = "json", uid: str = Depends(current_uid)):
    """Owner-scoped export. format=csv → flat CSV of every set, STREAMED from a
    server-side cursor so memory stays flat regardless of history size. format=json
    → JSON dump with each table capped so a huge history can't OOM the process."""
    if format == "csv":
        async def gen():
            import csv as _csv
            buf = io.StringIO(); wr = _csv.writer(buf)
            def flush():
                v = buf.getvalue(); buf.seek(0); buf.truncate(0); return v
            wr.writerow(["date", "focus", "exercise", "set", "weight_kg", "reps", "duration_s", "warmup"])
            yield flush().encode("utf-8-sig")
            async with get_session() as s:
                res = await s.stream(text(
                    """SELECT w.workout_date, w.focus_label, es.exercise_name, es.set_number,
                              es.weight_kg, es.reps, es.duration_seconds, es.is_warmup
                       FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
                       WHERE w.user_id = :u ORDER BY es.workout_id, es.set_number"""), {"u": uid})
                async for row in res:
                    wr.writerow([row[0], row[1], row[2], row[3], _to_f(row[4]), row[5], row[6], row[7]])
                    yield flush().encode("utf-8")
        return StreamingResponse(gen(), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="workouts.csv"'})
    from fastapi.encoders import jsonable_encoder
    CAP = 100000
    workouts = await _rows("SELECT id, workout_date, focus_label, notes, finished_at FROM workouts WHERE user_id=:u ORDER BY workout_date LIMIT :lim", u=uid, lim=CAP)
    sets = await _rows(
        """SELECT es.workout_id, es.exercise_name, es.set_number, es.weight_kg, es.reps,
                  es.duration_seconds, es.is_warmup
           FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
           WHERE w.user_id=:u ORDER BY es.workout_id, es.set_number LIMIT :lim""", u=uid, lim=CAP)
    plans = await _rows("SELECT planned_date, focus_label, exercises, notes, status FROM planned_workouts WHERE user_id=:u ORDER BY planned_date LIMIT :lim", u=uid, lim=CAP)
    meas = await _rows("SELECT * FROM body_measurements WHERE user_id=:u ORDER BY taken_on LIMIT :lim", u=uid, lim=CAP)
    routines = await _rows("SELECT name, days, created_at FROM routines WHERE user_id=:u ORDER BY id LIMIT :lim", u=uid, lim=CAP)
    payload = jsonable_encoder({
        "exported_at": (await today_for(uid)).isoformat(),
        "user_id": uid, "workouts": workouts, "sets": sets,
        "plans": plans, "measurements": meas, "routines": routines,
    })
    return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="fitness-export.json"'})


@app.get("/api/reports")
async def period_report(
    days: Optional[int] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    uid: str = Depends(current_uid),
):
    """Streams a PDF report for a period: presets (?days=7|14|30|60) or an
    explicit range (?from=YYYY-MM-DD&to=YYYY-MM-DD)."""
    today = await today_for(uid)
    if from_ or to:
        try:
            fd = date.fromisoformat((from_ or "").strip())
            td = date.fromisoformat((to or "").strip())
        except ValueError:
            raise HTTPException(422, "bad date, expected YYYY-MM-DD")
    else:
        n = days if (days and 1 <= days <= 366) else 30
        td, fd = today, today - timedelta(days=n)
    if fd > td:
        raise HTTPException(422, "from must be <= to")
    try:
        from app.bot.services.report_builder import build_period_report
    except Exception:
        raise HTTPException(503, "отчёты недоступны")
    # A bot lets the report pull the user's Telegram-stored photos; without a
    # valid token the report still builds (photos are skipped). Local web photos
    # (phase 3.6) are read from disk by the builder when telegram_file_id is null.
    bot = None
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    try:
        if token:
            from aiogram import Bot
            bot = Bot(token=token)
    except Exception:
        bot = None
    try:
        pdf = await build_period_report(bot, uid, fd, td)
    except Exception as e:
        raise HTTPException(500, f"ошибка построения отчёта: {str(e)[:200]}")
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass
    fn = f"report_{fd.isoformat()}_{td.isoformat()}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'})


# ──────────────────────────────── voice ─────────────────────────────────────

@app.post("/api/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...), uid: str = Depends(current_uid)):
    """Transcribe an uploaded audio clip (Whisper) → {text}. The caller then feeds
    the text into the existing set/measurement parsers."""
    data = await file.read()
    if not data:
        raise HTTPException(422, "пустой файл")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "файл слишком большой")
    await check_and_bump_ai(uid)
    try:
        from app.bot.services.ai_parser import transcribe_voice
    except Exception:
        raise HTTPException(503, "транскрипция недоступна")
    try:
        text_out = await transcribe_voice(data, filename=file.filename or "voice.webm")
    except Exception as e:
        raise HTTPException(502, f"ошибка распознавания: {str(e)[:200]}")
    return {"text": text_out or ""}


# ──────────────────────────────── photos ────────────────────────────────────

def _photo_path(storage_key: str) -> str:
    # storage_key is a generated uuid filename — guard against path traversal.
    safe = os.path.basename(storage_key or "")
    if not safe or safe != storage_key:
        raise HTTPException(404, "not found")
    return os.path.join(PHOTO_DIR, safe)


@app.post("/api/photos")
async def upload_photos(
    files: list[UploadFile] = File(...),
    notes: Optional[str] = Form(None),
    uid: str = Depends(current_uid),
):
    """Upload 1..N progress photos: resize → store on disk (PHOTO_DIR) → one
    series_id per upload → AI series description (under the daily limit)."""
    files = [f for f in files if f is not None][:10]
    if not files:
        raise HTTPException(422, "нет файлов")
    try:
        os.makedirs(PHOTO_DIR, exist_ok=True)
    except Exception:
        raise HTTPException(503, "хранилище фото недоступно")
    try:
        from app.bot.services.report_builder import _resize_photo
    except Exception:
        _resize_photo = None
    saved: list[tuple[str, bytes]] = []
    for f in files:
        raw = await f.read()
        if not raw:
            continue
        if len(raw) > 25 * 1024 * 1024:
            raise HTTPException(413, "файл слишком большой")
        jpeg = (_resize_photo(raw) if _resize_photo else raw) or raw
        key = uuid.uuid4().hex + ".jpg"
        try:
            with open(_photo_path(key), "wb") as fh:
                fh.write(jpeg)
        except Exception:
            raise HTTPException(503, "не удалось сохранить фото")
        saved.append((key, jpeg))
    if not saved:
        raise HTTPException(422, "пустые файлы")
    # One AI description per upload, under the daily cap; graceful on failure.
    short = detailed = None
    try:
        if not await _ai_over_limit(uid):
            from app.bot.services.photo_vision import describe_photo_series
            res = await describe_photo_series([(b, "image/jpeg") for _, b in saved])
            try:
                await check_and_bump_ai(uid)
            except HTTPException:
                pass
            if res:
                short, detailed = res.get("short"), res.get("detailed")
    except Exception:
        pass
    series_id = uuid.uuid4().hex
    today = await today_for(uid)
    note_val = (notes or "").strip() or None
    ids = []
    for key, _ in saved:
        ids.append(await db.create_web_photo(
            uid, today, key, series_id=series_id,
            ai_description=detailed, ai_description_short=short, notes=note_val))
    return {"series_id": series_id, "photo_ids": ids, "count": len(ids),
            "ai_short": short, "ai_detailed": detailed}


@app.get("/api/photos")
async def list_photos(limit: int = 30, uid: str = Depends(current_uid)):
    series = await db.get_photo_series(uid, max(1, min(limit, 200)))
    out = []
    for s in series:
        d = s.get("taken_on")
        out.append({
            "series_id": s["series_id"],
            "taken_on": d.isoformat() if isinstance(d, date) else d,
            "photo_ids": s.get("photo_ids") or [],
            "photo_count": s.get("photo_count") or len(s.get("photo_ids") or []),
            "ai_short": s.get("ai_description_short"),
            "ai_detailed": s.get("ai_description"),
            "notes": s.get("notes"),
        })
    return out


@app.get("/api/photos/{pid}/image")
async def photo_image(pid: int, uid: str = Depends(current_uid)):
    p = await db.get_photo(pid)
    if not p or p.get("user_id") != uid:
        raise HTTPException(404, "not found")
    key = p.get("storage_key")
    if not key:
        raise HTTPException(404, "нет файла")  # Telegram-only photo (no disk copy)
    path = _photo_path(key)
    if not os.path.isfile(path):
        raise HTTPException(404, "файл не найден")
    return StreamingResponse(open(path, "rb"), media_type="image/jpeg",
                             headers={"Cache-Control": "private, max-age=86400"})


@app.delete("/api/photos/series/{sid}")
async def del_photo_series(sid: str, uid: str = Depends(current_uid)):
    if sid.startswith("_solo_"):
        try:
            pid = int(sid.split("_", 2)[2])
        except (ValueError, IndexError):
            raise HTTPException(404, "not found")
        rows = await _rows("SELECT storage_key FROM progress_photos WHERE id=:i AND user_id=:u", i=pid, u=uid)
    else:
        rows = await _rows("SELECT storage_key FROM progress_photos WHERE series_id=:s AND user_id=:u", s=sid, u=uid)
    n = await db.delete_photo_series(uid, sid)   # scoped to uid inside
    for r in rows:
        k = r.get("storage_key")
        if k:
            try:
                os.remove(_photo_path(k))
            except Exception:
                pass
    return {"deleted": n}


# ──────────────────────────────── seed (dev) ────────────────────────────────

@app.post("/api/dev/seed")
async def seed(uid: str = Depends(current_uid)):
    if not ALLOW_SEED:
        raise HTTPException(403, "seed disabled in production")
    return await _seed(uid)


async def _seed(uid: str):
    today = await today_for(uid)
    monday = today - timedelta(days=today.weekday())

    def ex(name, sets, reps, weight=None, dur=None):
        d = {"name": name, "target_sets": sets, "target_reps_min": reps, "target_reps_max": reps}
        if weight is not None:
            d["target_weight"] = weight
        if dur is not None:
            d["target_duration_seconds"] = dur
        return d

    await db.create_planned_workout(uid, today, "Грудь + трицепс", [
        ex("Жим штанги лёжа", 3, 8, 80), ex("Жим гантелей под углом", 3, 12, 30),
        ex("Отжимания на брусьях", 3, 8), ex("Планка", 3, None, dur=60)])
    await db.create_planned_workout(uid, monday + timedelta(days=2), "Спина + бицепс", [
        ex("Подтягивания широким хватом", 4, 8), ex("Тяга штанги в наклоне", 4, 8, 70)])

    async def past(days_ago, focus, items):
        wid = await db.create_workout(uid, today - timedelta(days=days_ago), focus, None)
        for nm, sets in items:
            for wt, rp in sets:
                await db.add_set(wid, nm, weight_kg=wt, reps=rp)
        await db.finish_workout(wid)

    await past(7, "Грудь + трицепс", [("Жим штанги лёжа", [(80, 10), (80, 8), (75, 10)])])
    for i, (wkg, waist) in enumerate([(83.1, 86), (82.1, 84)]):
        await db.create_measurement(uid, today - timedelta(days=(1 - i) * 7),
                                    {"weight_kg": wkg, "waist_cm": waist, "chest_cm": 104, "arm_cm": 38})
    return {"seeded": True}


# ──────────────────────────── static frontend ───────────────────────────────

if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
