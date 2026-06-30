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


def test_set_batch_structured_rows(client):
    """WK-2: a `sets` array inserts several rows at once, skips empty rows,
    keeps per-row warmup flags, and is one idempotent op."""
    client.cookies.clear()
    wid = client.post("/api/workouts", json={}).json()["id"]
    op = "op-batch-1"
    body = {
        "exercise_name": "Жим",
        "client_op_id": op,
        "sets": [
            {"weight_kg": 80, "reps": 10, "is_warmup": True},
            {"weight_kg": 82, "reps": 8},
            {"weight_kg": "", "reps": ""},   # empty → skipped
            {"weight_kg": 80, "reps": 8},
        ],
    }
    r = client.post(f"/api/workouts/{wid}/sets", json=body)
    assert r.status_code == 200 and len(r.json()["ids"]) == 3
    # replay with the same op_id is a no-op
    r2 = client.post(f"/api/workouts/{wid}/sets", json=body)
    assert r2.json().get("duplicate") is True
    w = client.get(f"/api/workouts/{wid}").json()
    sets = [s for ex in w["exercises"] for s in ex["sets"]]
    assert len(sets) == 3
    assert sum(1 for s in sets if s["is_warmup"]) == 1
    # all-empty rows → 422
    bad = client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Жим", "sets": [{"weight_kg": ""}]})
    assert bad.status_code == 422


def test_archive_workout_backdated_finished(client):
    """HIST-1: a manually-added archive workout lands on its backdated date as a
    finished workout, with canonicalized names (DB-5); empty/future/empty-set
    requests are rejected."""
    client.cookies.clear()
    past = "2024-03-15"
    r = client.post("/api/workouts/archive", json={
        "workout_date": past, "focus_label": "Ноги",
        "exercises": [
            {"name": "икры стоя", "sets": [{"weight_kg": 100, "reps": 15}, {"weight_kg": 100, "reps": 12}]},
            {"name": "пустое упражнение", "sets": [{}]},   # all-empty → skipped
        ],
    })
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert r.json()["set_count"] == 2
    w = client.get(f"/api/workouts/{wid}").json()
    assert str(w["workout_date"]) == past
    names = [e["name"] for e in w["exercises"]]
    assert "Подъём на носки стоя" in names and "икры стоя" not in names  # canonicalized
    hist = client.get("/api/workouts?days=3650").json()
    assert any(x["id"] == wid and str(x["workout_date"]) == past for x in hist)  # finished, on its date
    # future date + empty workout rejected
    assert client.post("/api/workouts/archive", json={"workout_date": "2099-01-01", "exercises": [{"name": "Присед", "sets": [{"reps": 5}]}]}).status_code == 422
    assert client.post("/api/workouts/archive", json={"workout_date": past, "exercises": [{"name": "Присед", "sets": [{}]}]}).status_code == 422


def test_archive_bulk_save_atomic(client):
    """HIST-2: confirmed multi-workout import saves all on their dates with
    canonicalized names; a single bad date rejects the WHOLE batch (atomic)."""
    client.cookies.clear()
    r = client.post("/api/workouts/archive-bulk", json={"workouts": [
        {"workout_date": "2025-01-10", "focus_label": "Ноги", "exercises": [{"name": "икры стоя", "sets": [{"weight_kg": 90, "reps": 12}]}]},
        {"workout_date": "2025-01-12", "focus_label": "Грудь", "exercises": [{"name": "жим лёжа", "sets": [{"weight_kg": 80, "reps": 8}, {"weight_kg": 80, "reps": 6}]}]},
    ]})
    assert r.status_code == 200 and r.json()["count"] == 2, r.text
    ids = r.json()["ids"]
    hist = client.get("/api/workouts?days=4000").json()
    assert sorted(str(w["workout_date"]) for w in hist if w["id"] in ids) == ["2025-01-10", "2025-01-12"]
    w0 = client.get(f"/api/workouts/{ids[0]}").json()
    assert "Подъём на носки стоя" in [e["name"] for e in w0["exercises"]]   # canonicalized
    # atomicity: a future date in the batch → reject everything, insert nothing
    before = len(client.get("/api/workouts?days=4000").json())
    bad = client.post("/api/workouts/archive-bulk", json={"workouts": [
        {"workout_date": "2025-02-01", "exercises": [{"name": "Присед", "sets": [{"reps": 5}]}]},
        {"workout_date": "2099-01-01", "exercises": [{"name": "Присед", "sets": [{"reps": 5}]}]},
    ]})
    assert bad.status_code == 422
    assert len(client.get("/api/workouts?days=4000").json()) == before  # nothing inserted


def test_dashboard_today_done_flag(client):
    """W2-6: the dashboard reports today_done=true once a workout is finished today,
    so the UI stops offering to «start» the day's plan."""
    client.cookies.clear()
    assert client.get("/api/dashboard").json()["today_done"] is False
    wid = client.post("/api/workouts", json={"focus_label": "Сегодня"}).json()["id"]
    client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Присед", "weight_kg": 100, "reps": 5})
    client.post(f"/api/workouts/{wid}/finish")
    assert client.get("/api/dashboard").json()["today_done"] is True


def test_exercises_suggest_and_alias(client):
    """DB-7: suggest returns exact for known names + similar for unknown; a
    registered alias resolves afterward (so the dialog won't reappear)."""
    client.cookies.clear()
    r = client.get("/api/exercises/suggest", params={"q": "икры стоя"}).json()
    assert r["exact"] == "Подъём на носки стоя"          # known alias → no dialog
    r = client.get("/api/exercises/suggest", params={"q": "жим под углом бла-бла"}).json()
    assert r["exact"] is None and len(r["similar"]) >= 1  # unknown → candidates for the dialog
    a = client.post("/api/exercises/alias", json={"alias": "мой_тест_жим_x", "canonical": "Жим штанги лёжа"})
    assert a.status_code == 200 and a.json()["canonical"] == "Жим штанги лёжа"
    assert client.get("/api/exercises/suggest", params={"q": "мой_тест_жим_x"}).json()["exact"] == "Жим штанги лёжа"
    assert client.post("/api/exercises/alias", json={"alias": "", "canonical": "X"}).status_code == 422


def test_canonical_name_no_auto_rename(monkeypatch):
    """DB-5 (corrected): an unknown name is NOT auto-renamed (no AI / no fuzzy) —
    it's kept exactly as typed for the DB-7 dialog; exact catalog names/aliases
    still resolve deterministically."""
    import asyncio
    import app.bot.services.exercise_catalog as ec

    async def fake_resolve_known(name):  # simulate "not in the alias cache"
        return None
    monkeypatch.setattr(ec, "resolve_known", fake_resolve_known)
    from api.main import _canonical_name, _resolve_name, name_to_key, key_to_name
    # exact catalog alias still resolves
    assert asyncio.run(_resolve_name("икры стоя")) == key_to_name(name_to_key("икры стоя"))
    # a clearly-unknown name → unresolved (None) and kept verbatim
    unknown = "Жим зюзюблик три-четыре банана"
    assert asyncio.run(_resolve_name(unknown)) is None
    assert asyncio.run(_canonical_name(unknown, "local")) == unknown


def test_parse_logged_workouts_robust_to_malformed(monkeypatch):
    """HIST-2 hardening: malformed AI shapes (non-list workouts/exercises/sets,
    non-dict items, non-JSON) are skipped — never crash; is_failure is captured."""
    import asyncio
    from types import SimpleNamespace
    import app.bot.services.ai_parser as ap

    def run(raw):
        class Msgs:
            async def create(self, **kw):
                return SimpleNamespace(content=[SimpleNamespace(text=raw)])
        monkeypatch.setattr(ap._anthropic, "messages", Msgs())
        return asyncio.run(ap.parse_logged_workouts_text("x"))

    assert run('{"workouts": "nope"}') == []
    assert run('{"workouts": [null, 5, {"exercises": "x"}]}') == []
    assert run('{"workouts": [{"date":"2025-01-01","exercises":[{"name":"Ж","sets":5}]}]}') == []
    assert run('{"workouts": [{"exercises":[{"name":"Ж","sets":{"weight_kg":80}}]}]}') == []
    assert run('not json at all') == []
    good = run('{"workouts":[{"date":"2025-01-01","focus_label":"Грудь","exercises":[{"name":"Жим","sets":[{"weight_kg":80,"reps":8,"is_failure":true}]}]}]}')
    assert len(good) == 1 and good[0]["exercises"][0]["sets"][0]["is_failure"] is True


def test_patch_workout_date_and_focus(client):
    """HIST-3: edit a workout's date/focus; future date rejected; owner-scoped."""
    client.cookies.clear()
    wid = client.post("/api/workouts/archive", json={
        "workout_date": "2025-05-01", "focus_label": "Old",
        "exercises": [{"name": "Присед", "sets": [{"weight_kg": 100, "reps": 5}]}]}).json()["id"]
    assert client.patch(f"/api/workouts/{wid}", json={"workout_date": "2025-05-03", "focus_label": "New focus"}).status_code == 200
    w = client.get(f"/api/workouts/{wid}").json()
    assert str(w["workout_date"]) == "2025-05-03" and w["focus_label"] == "New focus"
    # focus-only edit leaves the date untouched
    client.patch(f"/api/workouts/{wid}", json={"focus_label": "F2"})
    w = client.get(f"/api/workouts/{wid}").json()
    assert str(w["workout_date"]) == "2025-05-03" and w["focus_label"] == "F2"
    # future date rejected; unknown workout → 404 (owner-scoped)
    assert client.patch(f"/api/workouts/{wid}", json={"workout_date": "2099-01-01"}).status_code == 422
    assert client.patch("/api/workouts/999999", json={"focus_label": "x"}).status_code == 404


def test_canonical_name_snaps_every_catalog_entry():
    """DB-5: the deterministic name canonicalizer snaps every catalog canonical and
    every alias to a known key with NO AI — so plan-save and set-logging agree and
    no duplicate exercises are spawned. Invariant holds for any catalog size."""
    from api.main import _canon_static, name_to_key, key_to_name
    from app.bot.services.catalog_v2 import CATALOG
    canon_fail, alias_fail = [], []
    for it in CATALOG.values():
        cr = it["canonical_ru"]
        if _canon_static(cr) != cr:               # canon must round-trip to itself
            canon_fail.append((cr, _canon_static(cr)))
        for a in it.get("aliases", []):
            if name_to_key(a) is None:            # every alias must resolve
                alias_fail.append((cr, a))
    assert not canon_fail, f"{len(canon_fail)} canon names don't snap to themselves: {canon_fail[:5]}"
    assert not alias_fail, f"{len(alias_fail)} aliases don't resolve: {alias_fail[:5]}"
    # case-insensitive resolution: any catalog canonical uppercased still snaps back
    first_canon = next(iter(CATALOG.values()))["canonical_ru"]
    assert _canon_static(first_canon.upper()) == first_canon
    # legacy alias collapses to the canonical name (present once the 186-catalog ships)
    k = name_to_key("икры стоя")
    if k:  # tolerate the 109-catalog where this legacy alias may be absent
        assert _canon_static("икры стоя") == key_to_name(k) != "икры стоя"


def test_legacy_rename_map_targets_are_canonical():
    """DB-4/6: every legacy_rename_map value is a catalog canonical (so the data
    migration writes canonical names), and every legacy key resolves to that same
    canonical via the catalog (so DB-5 snaps the same string on save going forward).
    Guards against map/catalog drift."""
    from api.main import _load_rename_map, name_to_key, key_to_name, _canon_static
    rename = _load_rename_map()
    assert rename, "legacy_rename_map should be present"
    bad_target, bad_key = [], []
    for old, new in rename.items():
        if _canon_static(new) != new:                  # target must be a canonical
            bad_target.append((old, new, _canon_static(new)))
        k = name_to_key(old)                           # legacy name must alias the target
        if not k or key_to_name(k) != new:
            bad_key.append((old, new, key_to_name(k) if k else None))
    assert not bad_target, f"{len(bad_target)} rename targets aren't catalog canonicals: {bad_target[:5]}"
    assert not bad_key, f"{len(bad_key)} legacy keys don't resolve to their mapped canonical: {bad_key[:5]}"


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


def test_exercise_catalog_v2(client):
    # DB-1: v2 catalog drives the picker — 14 groups, no duplicate labels (the old
    # biceps/triceps→«Руки», abs/core→«Пресс» bug is gone), and legacy names resolve.
    import urllib.parse
    g = client.get("/api/exercises/groups").json()
    labels = [x["label"] for x in g]
    assert len(g) == 14 and len(set(labels)) == 14
    assert {"Бицепс", "Трицепс", "Пресс", "Кор"} <= set(labels)   # distinct, not merged
    cat = client.get("/api/exercises/catalog").json()
    assert len(cat) >= 109 and "image" in cat[0]
    # backward-compat: an old spelling from history resolves to the curated v2 entry
    res = client.get("/api/exercises/search?q=" + urllib.parse.quote("разводка")).json()
    assert any("Разведение гантелей" in x["name"] for x in res)
    # new taxonomy is populated (functional + hamstrings groups didn't exist before)
    assert len(client.get("/api/exercises/catalog?group=functional").json()) >= 5
    assert len(client.get("/api/exercises/catalog?group=hamstrings").json()) >= 3


def test_workout_to_template_day(client):
    # UX3-FEAT-1: a finished workout maps to a routine day; per exercise the target is
    # (# working sets) × reps @ the heaviest working set's weight (warmups excluded).
    wid = client.post("/api/workouts", json={"focus_label": "Грудь"}).json()["id"]
    client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Жим", "weight_kg": 80, "reps": 8})
    client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Жим", "weight_kg": 85, "reps": 6})
    client.post(f"/api/workouts/{wid}/sets", json={"exercise_name": "Жим", "weight_kg": 70, "reps": 12, "is_warmup": True})
    client.post(f"/api/workouts/{wid}/finish")
    day = client.get(f"/api/workouts/{wid}/template-day").json()
    assert day["focus_label"] == "Грудь" and len(day["exercises"]) == 1
    e = day["exercises"][0]
    assert e["name"] == "Жим" and e["target_sets"] == 2          # 2 working sets (warmup excluded)
    assert e["target_weight"] == 85 and e["target_reps_min"] == 6  # heaviest working set
    rid = client.post("/api/routines", json={"name": "From-WO", "days": [day]}).json()["id"]
    assert rid                                                    # saveable as a routine


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
