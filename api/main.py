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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text

import app.db as db
from app.db.engine import engine, get_session
from app.bot.services.set_parser import parse_exercise_input
from app.modules.fitness.exercise_normalizer import EXERCISE_LIBRARY, possible_matches

from api.schema import CREATE_SQL
from api import auth

APP_ENV = os.getenv("APP_ENV", "development")
IS_PROD = APP_ENV == "production"
WEB_ORIGIN = os.getenv("WEB_ORIGIN", "http://localhost:8000")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")
ALLOW_SEED = (not IS_PROD) or os.getenv("SEED") == "1"
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
COOKIE = "session"

PUBLIC = ("/api/auth/telegram", "/api/config", "/healthz")

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
        if not uid and not IS_PROD:
            uid = os.getenv("DEV_UID", "local")  # local dev: no Telegram needed
        if not uid:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        request.state.uid = uid
    return await call_next(request)


def current_uid(request: Request) -> str:
    uid = getattr(request.state, "uid", None)
    if not uid:
        raise HTTPException(401, "unauthorized")
    return uid


@app.on_event("startup")
async def _startup() -> None:
    async with engine.begin() as conn:
        for stmt in (s.strip() for s in CREATE_SQL.split(";") if s.strip()):
            await conn.execute(text(stmt))
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
    if not auth.is_allowed(uid):
        raise HTTPException(403, "доступ не разрешён")
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

@app.get("/api/plans/today")
async def plan_today(uid: str = Depends(current_uid)):
    rows = await db.get_planned_workouts_range(uid, date.today(), date.today())
    return rows[0] if rows else None


@app.get("/api/plans/{pid}")
async def plan_detail(pid: int, uid: str = Depends(current_uid)):
    p = await db.get_planned_workout(pid)
    if not p:
        raise HTTPException(404, "plan not found")
    return p


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
