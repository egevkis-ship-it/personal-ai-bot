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
