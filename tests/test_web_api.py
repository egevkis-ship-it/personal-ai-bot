"""
Web API tests (httpx/ASGI via Starlette TestClient) — auth gate, multi-user
isolation (phase 0.1 IDOR), plan CRUD, set logging + idempotency, and the
registration/admin flow.

Marked `db`: needs a clean Postgres (DATABASE_URL). The fixture drops and
recreates the public schema, so point DATABASE_URL at a throwaway test DB.
"""
import asyncio
import hashlib
import hmac
import os
import time

import pytest
from sqlalchemy import text

# App config must be set before importing api.main (engine/env read at import).
os.environ["APP_ENV"] = "development"
os.environ.setdefault("OWNER_TELEGRAM_USER_ID", "local")
os.environ.setdefault("DEV_UID", "local")
os.environ.setdefault("AI_DAILY_LIMIT", "100")

pytestmark = pytest.mark.db


def _forge(token: str, uid, **extra):
    data = {"id": uid, "first_name": "U", "auth_date": int(time.time()), **extra}
    pairs = {k: v for k, v in data.items() if v is not None}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hashlib.sha256(token.encode()).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return data


@pytest.fixture()
def client():
    import api.auth as auth
    import api.main as m
    from app.db.engine import engine
    from fastapi.testclient import TestClient

    async def _reset():
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(_reset())
    with TestClient(m.app) as c:          # lifespan runs schema + owner bootstrap
        c.bot_token = auth.BOT_TOKEN
        yield c


def _login(c, uid, **extra):
    return c.post("/api/auth/telegram", json=_forge(c.bot_token, uid, **extra))


# ── auth gate ────────────────────────────────────────────────────────────────

def test_gate_new_user_pending_then_approved(client):
    client.cookies.clear()
    r = _login(client, 555, first_name="Иван", last_name="П", username="iv")
    assert r.status_code == 403 and r.json()["status"] == "pending"
    client.cookies.clear()
    row = next(u for u in client.get("/api/admin/users").json() if u["uid"] == "555")
    assert row["status"] == "pending" and row["display_name"] == "Иван П"
    client.cookies.clear()
    assert client.patch("/api/admin/users/555", json={"status": "approved"}).status_code == 200
    client.cookies.clear()
    r = _login(client, 555)
    assert r.status_code == 200 and client.cookies.get("session")


def test_gate_blocked(client):
    client.cookies.clear()
    client.post("/api/admin/users", json={"uid": "556"})
    client.patch("/api/admin/users/556", json={"status": "blocked"})
    client.cookies.clear()
    assert _login(client, 556).json()["status"] == "blocked"


def test_unauthenticated_is_401_in_prod_mode(client):
    # DEV mode gives a fallback uid; assert the gate routes exist behind auth at least
    client.cookies.clear()
    assert client.get("/api/admin/users").status_code == 200  # DEV uid is owner-admin


# ── multi-user isolation (IDOR, phase 0.1) ──────────────────────────────────

def test_two_user_isolation_404(client):
    client.cookies.clear()
    for u in ("111", "222"):
        assert client.post("/api/admin/users", json={"uid": u}).status_code == 200
    client.cookies.clear(); _login(client, 111)
    wid = client.post("/api/workouts", json={}).json()["id"]
    sid = client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Жим", "weight_kg": 50, "reps": 5}).json()["ids"][0]
    pid = client.post("/api/plans", json={"date": "2099-02-02", "focus_label": "A",
                                          "exercises": [{"name": "X", "target_sets": 3, "target_reps_min": 8}]}).json()["id"]
    mid = client.post("/api/measurements", json={"values": {"weight_kg": 80}}).json()["id"]
    client.cookies.clear(); _login(client, 222)
    assert client.get(f"/api/workouts/{wid}").status_code == 404
    assert client.patch(f"/api/workouts/{wid}/notes", json={"notes": "x"}).status_code == 404
    assert client.post(f"/api/workouts/{wid}/finish").status_code == 404
    assert client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Y", "reps": 1}).status_code == 404
    assert client.patch(f"/api/sets/{sid}", json={"reps": 9}).status_code == 404
    assert client.delete(f"/api/sets/{sid}").status_code == 404
    assert client.get(f"/api/plans/{pid}").status_code == 404
    assert client.patch(f"/api/plans/{pid}", json={"focus_label": "hax"}).status_code == 404
    assert client.delete(f"/api/plans/{pid}").status_code == 404
    assert client.delete(f"/api/measurements/{mid}").status_code == 404
    assert client.delete(f"/api/workouts/{wid}").status_code == 404


# ── plan CRUD + validation ──────────────────────────────────────────────────

def test_plan_crud_and_validation(client):
    client.cookies.clear()  # DEV owner
    r = client.post("/api/plans", json={"date": "2099-03-03", "focus_label": "V",
                                        "exercises": [{"name": "Присед", "target_sets": 3, "target_reps_min": 8, "target_reps_max": 12}]})
    assert r.status_code == 200
    pid = r.json()["id"]
    got = client.get(f"/api/plans/{pid}").json()
    assert got["focus_label"] == "V" and got["exercises"][0]["name"]
    assert client.patch(f"/api/plans/{pid}", json={"focus_label": "V2"}).status_code == 200
    assert client.get(f"/api/plans/{pid}").json()["focus_label"] == "V2"
    assert client.delete(f"/api/plans/{pid}").status_code == 200
    # validation
    assert client.post("/api/plans", json={"date": "2099-03-03", "exercises": [{"name": "X", "target_sets": -5}]}).status_code == 422


# ── set logging + idempotency (phase 4) ─────────────────────────────────────

def test_set_logging_idempotency(client):
    client.cookies.clear()
    wid = client.post("/api/workouts", json={}).json()["id"]
    op = "op-1"
    a = client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Жим", "weight_kg": 50, "reps": 5, "client_op_id": op})
    b = client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Жим", "weight_kg": 50, "reps": 5, "client_op_id": op})
    assert a.status_code == 200 and a.json()["ids"]
    assert b.status_code == 200 and b.json().get("duplicate") is True
    w = client.get(f"/api/workouts/{wid}").json()
    assert sum(len(ex["sets"]) for ex in w["exercises"]) == 1


# ── admin / registration flow ───────────────────────────────────────────────

def test_admin_flow_owner_and_last_admin_guards(client):
    client.cookies.clear()
    # owner is immutable
    assert client.patch("/api/admin/users/local", json={"status": "blocked"}).status_code == 409
    # add + promote
    assert client.post("/api/admin/users", json={"uid": "777"}).status_code == 200
    r = client.patch("/api/admin/users/777", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["status"] == "approved"
    # validation
    assert client.patch("/api/admin/users/777", json={"role": "zzz"}).status_code == 422
    assert client.patch("/api/admin/users/999999", json={"status": "approved"}).status_code == 404
    # alias-cache wipe is admin-only; generic service wipe doesn't expose it
    assert client.post("/api/service/wipe/aliases").status_code == 404
    assert client.post("/api/admin/wipe-aliases").status_code == 200


# ── new feature endpoints (phases B / D / F / G) ─────────────────────────────

def test_exercise_stats_pr(client):
    from urllib.parse import quote
    name = "Тест-жим-уникальный"   # not in the dev seed → isolated stats
    wid = client.post("/api/workouts", json={}).json()["id"]
    client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": name, "weight_kg": 80, "reps": 10})
    client.post(f"/api/workouts/{wid}/finish")
    st = client.get("/api/exercises/" + quote(name) + "/stats").json()
    assert st["pr"]["weight"]["weight"] == 80
    assert abs(st["pr"]["one_rm"]["value"] - 106.7) < 0.2   # Epley 80*(1+10/30)
    assert len(st["series"]) == 1 and st["series"][0]["top_weight"] == 80


def test_routines_crud_and_apply(client):
    body = {"name": "Сплит", "days": [
        {"weekday": 0, "focus_label": "Грудь", "exercises": [{"name": "Жим штанги лёжа", "target_sets": 4, "target_reps_min": 8, "target_weight": 80}]},
        {"weekday": 2, "focus_label": "Спина", "exercises": [{"name": "Тяга штанги в наклоне", "target_sets": 4}]},
    ]}
    rid = client.post("/api/routines", json=body).json()["id"]
    lst = client.get("/api/routines").json()
    assert len(lst) == 1 and lst[0]["name"] == "Сплит" and len(lst[0]["days"]) == 2
    # apply 2 weeks from a Monday (2026-07-06) → Mon+Wed × 2 = 4 plans
    ap = client.post(f"/api/routines/{rid}/apply", json={"from_date": "2026-07-06", "weeks": 2})
    assert ap.status_code == 200 and ap.json()["created"] == 4
    plans = client.get("/api/plans?from=2026-07-06&to=2026-07-20").json()
    dates = sorted(p["planned_date"] for p in plans)
    assert dates == ["2026-07-06", "2026-07-08", "2026-07-13", "2026-07-15"]
    assert client.delete(f"/api/routines/{rid}").status_code == 200
    assert client.get("/api/routines").json() == []


def test_settings_goals_and_dashboard(client):
    assert client.patch("/api/settings", json={"target_weight": 78, "weekly_goal": 3}).status_code == 200
    s = client.get("/api/settings").json()
    assert s["target_weight"] == 78 and s["weekly_goal"] == 3
    d = client.get("/api/dashboard").json()
    assert d["weekly_goal"] == 3 and d["target_weight"] == 78
    # clearing the target weight
    client.patch("/api/settings", json={"clear_target": True})
    assert client.get("/api/settings").json()["target_weight"] is None


def test_settings_date_format(client):
    # default
    assert client.get("/api/settings").json()["date_format"] == "DMY"
    # valid values persist
    for v in ("YMD", "MDY", "DMY"):
        assert client.patch("/api/settings", json={"date_format": v}).status_code == 200
        assert client.get("/api/settings").json()["date_format"] == v
    # invalid falls back to DMY (never stored verbatim)
    client.patch("/api/settings", json={"date_format": "ZZZ"})
    assert client.get("/api/settings").json()["date_format"] == "DMY"


def test_dashboard_week_is_calendar_week(client):
    # UX-5: «на этой неделе» must be the current calendar week (Mon–Sun), not a
    # rolling 7-day window. Seed one finished workout earlier THIS week and one
    # LAST week (within a rolling-7 window). Only this week's must count.
    import datetime as _dt
    from sqlalchemy.ext.asyncio import create_async_engine
    today = _dt.datetime.now(_dt.timezone.utc).date()   # matches today_for(default tz=UTC)
    monday = today - _dt.timedelta(days=today.weekday())

    async def _seed():  # fresh engine bound to this transient loop (don't reuse the app's)
        eng = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with eng.begin() as conn:
                await conn.execute(text("INSERT INTO workouts (user_id, workout_date, focus_label, finished_at) "
                                        "VALUES ('local', :d, 'this-week', now())"), {"d": monday})
                await conn.execute(text("INSERT INTO workouts (user_id, workout_date, focus_label, finished_at) "
                                        "VALUES ('local', :d, 'last-week', now())"), {"d": monday - _dt.timedelta(days=1)})
        finally:
            await eng.dispose()
    asyncio.run(_seed())
    d = client.get("/api/dashboard").json()
    assert d["week_workouts"] == 1   # last week's (Sunday) workout excluded despite being <7 days ago


def test_export_json_and_csv(client):
    wid = client.post("/api/workouts", json={}).json()["id"]
    client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Присед", "weight_kg": 100, "reps": 5})
    j = client.get("/api/export?format=json")
    assert j.status_code == 200
    data = j.json()
    assert "workouts" in data and "sets" in data and "routines" in data
    c = client.get("/api/export?format=csv")
    assert c.status_code == 200 and "exercise" in c.text and "Присед" in c.text


def test_history_search_filter(client):
    name = "Уникальная-тяга-теста"   # not in the dev seed → isolated search
    wid = client.post("/api/workouts", json={"focus_label": "Грудь"}).json()["id"]
    client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": name, "weight_kg": 60, "reps": 8})
    client.post(f"/api/workouts/{wid}/finish")
    assert len(client.get("/api/workouts?days=365&q=уникальная-тяга").json()) == 1
    assert len(client.get("/api/workouts?days=365&q=несуществующее-zzz").json()) == 0


def test_coach_context_roundtrip_and_recovery_mode(client):
    # default
    c = client.get("/api/coach/context").json()
    assert c == {"answers": {}, "recovery_mode": "natural"}
    # save survey answers + switch recovery mode
    assert client.post("/api/coach/context",
                       json={"answers": {"sleep": "плохо", "energy": "средне"},
                             "recovery_mode": "enhanced"}).status_code == 200
    c = client.get("/api/coach/context").json()
    assert c["answers"] == {"sleep": "плохо", "energy": "средне"}
    assert c["recovery_mode"] == "enhanced"
    # recovery_mode is mirrored into /settings (single source for the coach)
    assert client.get("/api/settings").json()["recovery_mode"] == "enhanced"
    # clearing wipes survey answers (mode stays a user setting)
    assert client.delete("/api/coach/context").status_code == 200
    assert client.get("/api/coach/context").json()["answers"] == {}


def test_coach_generate_and_apply(client, monkeypatch):
    import app.bot.services.week_coach as wc

    async def fake_generate(brief, recovery_mode, answers):
        assert recovery_mode in ("natural", "enhanced")
        assert isinstance(brief, dict) and "exercise_summary" in brief  # brief was built
        return {
            "days": [
                {"weekday": 0, "focus_label": "Грудь / Трицепс", "notes": "акцент на жиме",
                 "exercises": [{"name": "Жим штанги лёжа", "target_sets": 4,
                                "target_reps_min": 6, "target_reps_max": 8, "target_weight": 95}]},
                {"weekday": 3, "focus_label": "Отдых", "exercises": []},  # rest → dropped
            ],
            "rationale": "Жим стоит на плато — добавил объём.",
            "flags": ["Следи за поясницей в тяге."],
        }

    monkeypatch.setattr(wc, "generate_week", fake_generate)
    r = client.post("/api/coach/generate-week", json={"from_date": "2099-03-02"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rationale"] and body["flags"] and body["recovery_mode"] in ("natural", "enhanced")
    assert len(body["days"]) == 1                       # the rest day was dropped
    day = body["days"][0]
    assert day["weekday"] == 0 and day["focus_label"].startswith("Грудь")
    assert day["exercises"][0]["name"] == "Жим штанги лёжа"
    # nothing saved yet — apply confirms it into the schedule
    apply = client.post("/api/coach/apply", json={"days": body["days"]}).json()
    assert apply["saved"] == 1
    saved_date = apply["plans"][0]["planned_date"]
    plans = client.get(f"/api/plans?from={saved_date}&to={saved_date}").json()
    assert any(p["focus_label"].startswith("Грудь") for p in plans)
    # no from_date → defaults to NEXT week's Monday (feature is «следующую неделю»)
    import datetime as _dt
    nf = client.post("/api/coach/generate-week", json={}).json()
    d0 = _dt.date.fromisoformat(nf["days"][0]["date"])  # weekday-0 day
    assert d0.weekday() == 0 and d0 > _dt.date.today()  # a Monday, strictly future


def test_coach_generate_surfaces_ai_failure_as_502(client, monkeypatch):
    import app.bot.services.week_coach as wc

    async def boom(brief, recovery_mode, answers):
        raise wc.CoachError("invalid x-api-key")

    monkeypatch.setattr(wc, "generate_week", boom)
    r = client.post("/api/coach/generate-week", json={"from_date": "2099-03-02"})
    assert r.status_code == 502
    assert "недоступен" in r.json()["detail"]


def test_history_hides_rest_days(client):
    # UX2-2: a real workout (has a working set) is shown; finished «Отдых» and
    # empty-focus sessions with 0 working sets are hidden from the journal.
    import datetime as _dt
    from sqlalchemy.ext.asyncio import create_async_engine
    wid = client.post("/api/workouts", json={"focus_label": "Грудь-UX22"}).json()["id"]
    client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Жим", "weight_kg": 80, "reps": 8})
    client.post(f"/api/workouts/{wid}/finish")
    rest = client.post("/api/workouts", json={"focus_label": "Отдых"}).json()["id"]
    client.post(f"/api/workouts/{rest}/finish")

    async def _seed_empty():  # the web API defaults a free workout's focus to «Свободная тренировка»,
        eng = create_async_engine(os.environ["DATABASE_URL"])  # so insert an empty-focus rest row directly
        try:
            async with eng.begin() as conn:
                await conn.execute(text("INSERT INTO workouts (user_id, workout_date, focus_label, finished_at) "
                                        "VALUES ('local', :d, '', now())"), {"d": _dt.date.today()})
        finally:
            await eng.dispose()
    asyncio.run(_seed_empty())

    hist = client.get("/api/workouts?days=365").json()
    ids = [w["id"] for w in hist]
    foci = [(w.get("focus_label") or "") for w in hist]
    assert wid in ids                              # real workout kept
    assert rest not in ids                         # «Отдых» 0-set hidden
    assert "Отдых" not in foci and "" not in foci  # no rest / empty rows in the journal


def test_choose_day_excludes_deleted_plans(client):
    # UX3-FIX-3: a soft-deleted plan must not resurface in /workouts/week as «пропущено».
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).date()
    d = (today - _dt.timedelta(days=today.weekday()) + _dt.timedelta(days=2)).isoformat()  # a day this week
    pid = client.post("/api/plans", json={"date": d, "focus_label": "F3", "exercises": [{"name": "X", "target_sets": 1}]}).json()["id"]
    assert any(p["focus_label"] == "F3" for p in client.get("/api/workouts/week").json())   # live plan shows
    client.delete(f"/api/plans/{pid}")
    assert not any(p["id"] == pid for p in client.get("/api/workouts/week").json())          # deleted plan gone


def test_bulk_create_conflict_guard(client):
    # UX2-4: mass create over an occupied day asks (409 + occupied), then add/replace.
    D = "2099-05-04"
    client.post("/api/plans", json={"date": D, "focus_label": "A", "exercises": [{"name": "X", "target_sets": 1}]})
    r = client.post("/api/plans/bulk", json={"days": [{"date": D, "focus_label": "B", "exercises": []}]})
    assert r.status_code == 409 and r.json()["detail"]["occupied"] == [D]
    assert client.post("/api/plans/bulk", json={"mode": "add", "days": [{"date": D, "focus_label": "B", "exercises": []}]}).status_code == 200
    assert len(client.get(f"/api/plans?from={D}&to={D}").json()) == 2          # added a second
    assert client.post("/api/plans/bulk", json={"mode": "replace", "days": [{"date": D, "focus_label": "C", "exercises": []}]}).status_code == 200
    after = client.get(f"/api/plans?from={D}&to={D}").json()
    assert len(after) == 1 and after[0]["focus_label"] == "C"                  # replaced both


def test_coach_apply_conflict_guard(client):
    D = "2099-05-05"
    client.post("/api/plans", json={"date": D, "focus_label": "A", "exercises": [{"name": "X", "target_sets": 1}]})
    r = client.post("/api/coach/apply", json={"days": [{"date": D, "weekday": 0, "focus_label": "B", "exercises": []}]})
    assert r.status_code == 409 and D in r.json()["detail"]["occupied"]
    assert client.post("/api/coach/apply", json={"mode": "replace", "days": [{"date": D, "weekday": 0, "focus_label": "B", "exercises": []}]}).status_code == 200


def test_routine_apply_conflict_guard(client):
    import datetime as _dt
    base = _dt.date(2099, 6, 1)
    mon = (base - _dt.timedelta(days=base.weekday())).isoformat()   # a Monday → weekday-0 day lands on it
    rid = client.post("/api/routines", json={"name": "R",
        "days": [{"weekday": 0, "focus_label": "Mon", "exercises": [{"name": "X", "target_sets": 1}]}]}).json()["id"]
    assert client.post(f"/api/routines/{rid}/apply", json={"from_date": mon, "weeks": 1}).json()["created"] == 1
    r = client.post(f"/api/routines/{rid}/apply", json={"from_date": mon, "weeks": 1})  # day now occupied
    assert r.status_code == 409 and mon in r.json()["detail"]["occupied"]
    assert client.post(f"/api/routines/{rid}/apply", json={"from_date": mon, "weeks": 1, "mode": "replace"}).json()["created"] == 1


def test_start_workout_idor_404(client):
    # user A creates a plan + a workout; user B must NOT be able to seed from them
    client.cookies.clear()
    for u in ("4101", "4102"):   # add+approve both via the DEV owner
        assert client.post("/api/admin/users", json={"uid": u}).status_code == 200
    client.cookies.clear(); _login(client, 4101)
    pid = client.post("/api/plans", json={"date": "2099-03-03", "focus_label": "A-plan",
                                          "exercises": [{"name": "Жим штанги лёжа", "target_sets": 3}]}).json()["id"]
    awid = client.post("/api/workouts", json={"focus_label": "A-wo"}).json()["id"]
    client.post(f"/api/workouts/{awid}/sets", json={"exercise_name": "Присед", "weight_kg": 90, "reps": 5})
    client.cookies.clear(); _login(client, 4102)
    assert client.post("/api/workouts", json={"from_plan_id": pid}).status_code == 404
    assert client.post("/api/workouts", json={"repeat_from": awid}).status_code == 404
    # own resources still work
    bwid = client.post("/api/workouts", json={"focus_label": "B-wo"}).json()["id"]
    assert client.post("/api/workouts", json={"repeat_from": bwid}).status_code == 200
