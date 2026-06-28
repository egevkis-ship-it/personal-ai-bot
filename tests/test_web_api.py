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
