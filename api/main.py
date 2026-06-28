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

import os
from datetime import date, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text

import app.db as db
from app.db.engine import engine, get_session
from app.bot.services import tz
from app.bot.services.set_parser import parse_exercise_input
from app.modules.fitness.exercise_normalizer import EXERCISE_LIBRARY, possible_matches

from api.schema import CREATE_SQL
from api import auth

APP_ENV = os.getenv("APP_ENV", "development")
IS_PROD = APP_ENV == "production"
WEB_ORIGIN = os.getenv("WEB_ORIGIN", "http://localhost:8000")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")
ALLOW_SEED = (not IS_PROD) or os.getenv("SEED") == "1"
OWNER_UID = os.getenv("OWNER_TELEGRAM_USER_ID", "").strip()
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
COOKIE = "session"

PUBLIC = ("/api/auth/telegram", "/api/auth/logout", "/api/config", "/healthz")

GROUP_RU = {
    "chest": "Грудь", "back": "Спина", "legs": "Ноги", "shoulders": "Плечи",
    "biceps": "Руки", "triceps": "Руки", "abs": "Пресс", "core": "Пресс",
    "calves": "Икры", "glutes": "Ягодицы", "posterior_chain": "Задняя цепь",
    "cardio": "Кардио",
}

_NAME_TO_KEY: dict[str, str] = {}
_KEY_TO_RU: dict[str, str] = {}
for _k, _it in EXERCISE_LIBRARY.items():
    _KEY_TO_RU[_k] = _it["canonical_ru"]
    _NAME_TO_KEY[_it["canonical_ru"].lower()] = _k
    for _a in _it.get("aliases", []):
        _NAME_TO_KEY.setdefault(_a.lower(), _k)


def key_to_name(key): return None if not key else _KEY_TO_RU.get(key, key)
def name_to_key(name): return None if not name else _NAME_TO_KEY.get(name.strip().lower())
def _to_f(v): return float(v) if v is not None else None


def exercise_type(name: str, key: str | None = None) -> str:
    n = (name or "").lower()
    mg = EXERCISE_LIBRARY.get(key or "", {}).get("muscle_group")
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


def current_uid(request: Request) -> str:
    uid = getattr(request.state, "uid", None)
    if not uid:
        raise HTTPException(401, "unauthorized")
    return uid


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


@app.on_event("startup")
async def _startup() -> None:
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
    if not IS_PROD:  # local dev convenience: seed demo data for the dev user
        dev = os.getenv("DEV_UID", "local")
        have = await _scalar("SELECT COUNT(*) FROM workouts WHERE user_id=:u", u=dev) or 0
        plans = await _scalar("SELECT COUNT(*) FROM planned_workouts WHERE user_id=:u", u=dev) or 0
        if have == 0 and plans == 0:
            await _seed(dev)


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


# what -> (callable, takes_uid). 'aliases' is a global cache (no uid).
_WIPE = {
    "plans": (db.wipe_planned_workouts, True),
    "history": (db.wipe_workouts, True),
    "measurements": (db.wipe_measurements, True),
    "photos": (db.wipe_photos, True),
    "aliases": (db.wipe_exercise_aliases, False),
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


# ────────────────────────────── dashboard ───────────────────────────────────

@app.get("/api/dashboard")
async def dashboard(uid: str = Depends(current_uid)):
    today = date.today()
    plans = await db.get_planned_workouts_range(uid, today, today)
    active = await db.get_active_workout(uid)
    last_m = await db.get_last_measurement(uid)
    week_count = await _scalar(
        "SELECT COUNT(*) FROM workouts WHERE user_id=:u AND finished_at IS NOT NULL AND workout_date >= :d",
        u=uid, d=today - timedelta(days=7))
    last_workout = await db.get_last_workout(uid)
    return {"today_plan": plans[0] if plans else None, "active_workout": active,
            "last_measurement": last_m, "week_workouts": week_count or 0,
            "last_workout": last_workout}


# ────────────────────────────── workouts ────────────────────────────────────

class StartWorkout(BaseModel):
    from_plan_id: Optional[int] = None
    repeat_from: Optional[int] = None
    focus_label: Optional[str] = None


@app.post("/api/workouts")
async def start_workout(body: StartWorkout, uid: str = Depends(current_uid)):
    today = date.today()
    if body.from_plan_id:
        plan = await db.get_planned_workout(body.from_plan_id)
        if not plan:
            raise HTTPException(404, "plan not found")
        wid = await db.create_workout(uid, today, plan.get("focus_label"), plan["id"])
        return {"id": wid}
    if body.repeat_from:
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


@app.get("/api/workouts/active")
async def active_workout(uid: str = Depends(current_uid)):
    w = await db.get_active_workout(uid)
    if not w:
        return None
    return await assemble_workout(uid, w["id"])


@app.get("/api/workouts/week")
async def workouts_week(uid: str = Depends(current_uid)):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    rows = await _rows(
        """
        SELECT pw.*,
          EXISTS(SELECT 1 FROM workouts w WHERE w.planned_workout_id = pw.id
                 AND w.finished_at IS NOT NULL) AS done
        FROM planned_workouts pw
        WHERE pw.user_id = :u AND pw.planned_date BETWEEN :a AND :b
        ORDER BY pw.planned_date ASC
        """, u=uid, a=monday, b=sunday)
    return [{"id": r["id"], "planned_date": r["planned_date"], "focus_label": r["focus_label"],
             "status": "completed" if r["done"] else r["status"],
             "is_today": r["planned_date"] == today, "exercises": r["exercises"]} for r in rows]


@app.get("/api/workouts")
async def workouts_history(days: int = 30, uid: str = Depends(current_uid)):
    today = date.today()
    rows = [r for r in await db.get_workouts_range(uid, today - timedelta(days=days), today) if r.get("finished_at")]
    out = []
    for w in rows:
        sets = await db.get_workout_sets(w["id"])
        working = [s for s in sets if not s["is_warmup"]]
        out.append({**w, "set_count": len(working),
                    "tonnage": round(sum((_to_f(s["weight_kg"]) or 0) * (s["reps"] or 0) for s in working))})
    out.reverse()
    return out


@app.get("/api/workouts/{wid}")
async def workout_detail(wid: int, uid: str = Depends(current_uid)):
    return await assemble_workout(uid, wid)


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
    await db.update_workout_notes(wid, body.notes)
    return {"ok": True}


@app.delete("/api/workouts/{wid}")
async def delete_workout(wid: int, uid: str = Depends(current_uid)):
    async with get_session() as s:
        await s.execute(text("DELETE FROM workouts WHERE id=:id AND user_id=:u"), {"id": wid, "u": uid})
    return {"ok": True}


@app.post("/api/workouts/{wid}/finish")
async def finish_workout(wid: int, uid: str = Depends(current_uid)):
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


@app.post("/api/workouts/{wid}/sets")
async def add_set(wid: int, body: AddSet, uid: str = Depends(current_uid)):
    if not await db.get_workout(wid):
        raise HTTPException(404, "workout not found")
    if body.text:
        last = await db.get_last_set(wid)
        parsed = parse_exercise_input(body.text, last["exercise_name"] if last else None)
        if not parsed:
            raise HTTPException(422, "не удалось разобрать ввод")
        ids = []
        for r in parsed:
            ids.append(await db.add_set(wid, r.exercise_name, weight_kg=r.weight_kg, reps=r.reps,
                       reps_text=r.reps_text, duration_seconds=r.duration_seconds,
                       superset_group=r.superset_group, is_warmup=r.is_warmup,
                       is_failure=r.is_failure, notes=r.notes))
        return {"ids": ids}
    name = body.exercise_name or key_to_name(body.exercise_key)
    if not name:
        raise HTTPException(422, "нужно упражнение")
    sid = await db.add_set(wid, name, weight_kg=body.weight_kg, reps=body.reps,
                           reps_text=body.reps_text, duration_seconds=body.duration_seconds,
                           superset_group=body.superset_group, is_warmup=body.is_warmup,
                           is_failure=body.is_failure)
    return {"ids": [sid]}


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
    await db.update_set(sid, **{k: v for k, v in body.dict().items() if v is not None})
    return {"ok": True}


@app.delete("/api/sets/{sid}")
async def del_set(sid: int, uid: str = Depends(current_uid)):
    await db.delete_set(sid)
    return {"ok": True}


# ────────────────────────────── exercises ───────────────────────────────────

@app.get("/api/exercises/recent")
async def exercises_recent(limit: int = 12, uid: str = Depends(current_uid)):
    rows = await _rows(
        """
        SELECT es.exercise_name AS name, MAX(es.created_at) AS last_at
        FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
        WHERE w.user_id = :u GROUP BY es.exercise_name ORDER BY last_at DESC LIMIT :lim
        """, u=uid, lim=limit)
    for r in rows:
        r["key"] = name_to_key(r["name"])
    return rows


@app.get("/api/exercises/groups")
async def exercises_groups(uid: str = Depends(current_uid)):
    counts: dict[str, int] = {}
    for it in EXERCISE_LIBRARY.values():
        g = it.get("muscle_group") or "other"
        counts[g] = counts.get(g, 0) + 1
    return [{"group": g, "label": GROUP_RU.get(g, g), "count": c} for g, c in counts.items()]


@app.get("/api/exercises/catalog")
async def exercises_catalog(group: Optional[str] = None, uid: str = Depends(current_uid)):
    out = [{"exercise_key": k, "name": it["canonical_ru"], "muscle_group": it.get("muscle_group")}
           for k, it in EXERCISE_LIBRARY.items() if not group or it.get("muscle_group") == group]
    out.sort(key=lambda x: x["name"])
    return out


@app.get("/api/exercises/search")
async def exercises_search(q: str, limit: int = 8, uid: str = Depends(current_uid)):
    return [{"exercise_key": r["exercise_key"], "name": r["canonical_ru"], "muscle_group": r.get("muscle_group")}
            for r in possible_matches(q, limit)]


@app.get("/api/exercises/{key}/history")
async def exercise_history(key: str, limit: int = 10, uid: str = Depends(current_uid)):
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


def _clean_plan_exercises(exercises: list[dict]) -> list[dict]:
    """Normalize incoming exercise dicts to the bot's planned-exercise shape."""
    out: list[dict] = []
    for e in exercises or []:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        d = {
            "name": name,
            "target_sets": e.get("target_sets"),
            "target_reps_min": e.get("target_reps_min"),
            "target_reps_max": e.get("target_reps_max"),
            "target_weight": _to_f(e.get("target_weight")),
            "reps_text": (e.get("reps_text") or None),
            "notes": (e.get("notes") or None),
            "superset_group": (e.get("superset_group") or None),
        }
        # if only one reps value provided, mirror it into min==max (bot convention)
        if d["target_reps_min"] is not None and d["target_reps_max"] is None:
            d["target_reps_max"] = d["target_reps_min"]
        out.append(d)
    return out


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


@app.get("/api/plans")
async def plans_list(days: int = 30, uid: str = Depends(current_uid)):
    """Upcoming planned workouts within the next `days` days (status='planned')."""
    today = date.today()
    rows = await db.get_planned_workouts_range(uid, today, today + timedelta(days=days))
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
    rows = await db.get_planned_workouts_range(uid, date.today(), date.today())
    return rows[0] if rows else None


@app.get("/api/plans/{pid}")
async def plan_detail(pid: int, uid: str = Depends(current_uid)):
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
    d = _resolve_plan_date(body.date, body.weekday, date.today())
    exs = _clean_plan_exercises([e.model_dump() for e in body.exercises])
    pid = await db.create_planned_workout(uid, d, (body.focus_label or None), exs)
    if body.notes:
        await db.update_planned_workout_notes(pid, body.notes)
    return {"id": pid, "planned_date": d.isoformat()}


@app.patch("/api/plans/{pid}")
async def update_plan(pid: int, body: UpdatePlan, uid: str = Depends(current_uid)):
    existing = await db.get_planned_workout(pid)
    if not existing:
        raise HTTPException(404, "plan not found")
    exs = None
    if body.exercises is not None:
        exs = _clean_plan_exercises([e.model_dump() for e in body.exercises])
    await db.update_planned_workout(
        pid,
        focus_label=body.focus_label,
        exercises=exs,
    )
    if body.notes is not None:
        await db.update_planned_workout_notes(pid, body.notes)
    if body.date is not None or body.weekday is not None:
        new_date = _resolve_plan_date(body.date, body.weekday, date.today())
        async with get_session() as s:
            await s.execute(
                text("UPDATE planned_workouts SET planned_date = :d, updated_at = now() WHERE id = :id"),
                {"d": new_date, "id": pid},
            )
    return {"ok": True}


@app.delete("/api/plans/{pid}")
async def delete_plan(pid: int, uid: str = Depends(current_uid)):
    await db.delete_planned_workout(pid)   # soft-delete: status='skipped' (mirrors bot)
    return {"ok": True}


@app.post("/api/plans/parse")
async def parse_plan(body: ParsePlan, uid: str = Depends(current_uid)):
    """Free-text → structured days preview (AI), with dates pre-assigned.
    Does NOT save — the client confirms via POST /api/plans/bulk."""
    if not (body.text or "").strip():
        raise HTTPException(422, "пустой текст")
    try:
        from app.bot.services.ai_parser import parse_plan_text
    except Exception:
        raise HTTPException(503, "ИИ-парсер недоступен")
    try:
        days = await parse_plan_text(body.text)
    except Exception as e:
        raise HTTPException(502, f"ошибка разбора: {str(e)[:200]}")
    if not days:
        raise HTTPException(422, "не удалось разобрать план")
    today = date.today()
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
                "name": ex.name, "target_sets": ex.target_sets,
                "target_reps_min": ex.target_reps_min, "target_reps_max": ex.target_reps_max,
                "target_weight": ex.target_weight, "reps_text": ex.reps_text,
                "notes": ex.notes, "superset_group": ex.superset_group,
            } for ex in day.exercises],
        })
    return {"days": out}


@app.post("/api/plans/bulk")
async def create_plans_bulk(body: BulkPlans, uid: str = Depends(current_uid)):
    """Save multiple planned days at once (confirmed preview)."""
    saved = []
    for day in body.days:
        d = _resolve_plan_date(day.date, day.weekday, date.today())
        exs = _clean_plan_exercises([e.model_dump() for e in day.exercises])
        pid = await db.create_planned_workout(uid, d, (day.focus_label or None), exs)
        if day.notes:
            await db.update_planned_workout_notes(pid, day.notes)
        saved.append({"id": pid, "planned_date": d.isoformat()})
    return {"saved": len(saved), "plans": saved}


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
    mid = await db.create_measurement(uid, body.taken_on or date.today().isoformat(), values)
    return {"id": mid}


@app.get("/api/measurements")
async def list_measurements(limit: int = 30, uid: str = Depends(current_uid)):
    return await db.get_measurements(uid, limit)


@app.get("/api/measurements/last")
async def last_measurement(uid: str = Depends(current_uid)):
    return await db.get_last_measurement(uid)


@app.delete("/api/measurements/{mid}")
async def del_measurement(mid: int, uid: str = Depends(current_uid)):
    await db.delete_measurement(mid)
    return {"ok": True}


# ──────────────────────────────── seed (dev) ────────────────────────────────

@app.post("/api/dev/seed")
async def seed(uid: str = Depends(current_uid)):
    if not ALLOW_SEED:
        raise HTTPException(403, "seed disabled in production")
    return await _seed(uid)


async def _seed(uid: str):
    today = date.today()
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
