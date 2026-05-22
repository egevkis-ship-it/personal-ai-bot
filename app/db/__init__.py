"""
Clean DB layer — 3 tables: planned_workouts, workouts, exercise_sets.
All user_id params are Telegram user-ID strings.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from app.db.engine import get_session


# ─────────────────────── planned_workouts ──────────────────────────────────

async def create_planned_workout(
    user_id: str,
    planned_date: str | date,
    focus_label: str | None,
    exercises: list[dict],
) -> int:
    d = planned_date if isinstance(planned_date, str) else planned_date.isoformat()
    async with get_session() as s:
        r = await s.execute(
            text("""
                INSERT INTO planned_workouts (user_id, planned_date, focus_label, exercises)
                VALUES (:uid, :d, :focus, CAST(:exs AS jsonb))
                RETURNING id
            """),
            {"uid": user_id, "d": d, "focus": focus_label, "exs": json.dumps(exercises, ensure_ascii=False)},
        )
        return r.scalar_one()


async def get_planned_workout(plan_id: int) -> dict | None:
    async with get_session() as s:
        r = await s.execute(text("SELECT * FROM planned_workouts WHERE id = :id"), {"id": plan_id})
        row = r.mappings().first()
        return dict(row) if row else None


async def get_planned_workouts_range(
    user_id: str,
    from_date: str | date,
    to_date: str | date,
) -> list[dict]:
    fd = from_date if isinstance(from_date, str) else from_date.isoformat()
    td = to_date if isinstance(to_date, str) else to_date.isoformat()
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM planned_workouts
                WHERE user_id = :uid
                  AND planned_date BETWEEN :fd AND :td
                  AND status = 'planned'
                ORDER BY planned_date ASC
            """),
            {"uid": user_id, "fd": fd, "td": td},
        )
        return [dict(row) for row in r.mappings().all()]


async def get_today_plan(user_id: str) -> dict | None:
    rows = await get_planned_workouts_range(user_id, date.today(), date.today())
    return rows[0] if rows else None


async def update_planned_workout(
    plan_id: int,
    *,
    focus_label: str | None = None,
    exercises: list[dict] | None = None,
    status: str | None = None,
) -> None:
    parts: list[str] = []
    params: dict = {"id": plan_id}
    if focus_label is not None:
        parts.append("focus_label = :focus"); params["focus"] = focus_label
    if exercises is not None:
        parts.append("exercises = CAST(:exs AS jsonb)")
        params["exs"] = json.dumps(exercises, ensure_ascii=False)
    if status is not None:
        parts.append("status = :status"); params["status"] = status
    if not parts:
        return
    parts.append("updated_at = now()")
    async with get_session() as s:
        await s.execute(text(f"UPDATE planned_workouts SET {', '.join(parts)} WHERE id = :id"), params)


async def delete_planned_workout(plan_id: int) -> None:
    async with get_session() as s:
        await s.execute(
            text("UPDATE planned_workouts SET status='skipped', updated_at=now() WHERE id=:id"),
            {"id": plan_id},
        )


# ────────────────────────────── workouts ───────────────────────────────────

async def create_workout(
    user_id: str,
    workout_date: str | date,
    focus_label: str | None,
    planned_workout_id: int | None = None,
) -> int:
    d = workout_date if isinstance(workout_date, str) else workout_date.isoformat()
    async with get_session() as s:
        r = await s.execute(
            text("""
                INSERT INTO workouts (user_id, workout_date, focus_label, planned_workout_id)
                VALUES (:uid, :d, :focus, :pid)
                RETURNING id
            """),
            {"uid": user_id, "d": d, "focus": focus_label, "pid": planned_workout_id},
        )
        return r.scalar_one()


async def finish_workout(workout_id: int) -> None:
    async with get_session() as s:
        await s.execute(
            text("UPDATE workouts SET finished_at = now() WHERE id = :id"),
            {"id": workout_id},
        )


async def get_workout(workout_id: int) -> dict | None:
    async with get_session() as s:
        r = await s.execute(text("SELECT * FROM workouts WHERE id = :id"), {"id": workout_id})
        row = r.mappings().first()
        return dict(row) if row else None


async def get_workouts_range(
    user_id: str,
    from_date: str | date,
    to_date: str | date,
) -> list[dict]:
    fd = from_date if isinstance(from_date, str) else from_date.isoformat()
    td = to_date if isinstance(to_date, str) else to_date.isoformat()
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM workouts
                WHERE user_id = :uid
                  AND workout_date BETWEEN :fd AND :td
                ORDER BY workout_date ASC, started_at ASC
            """),
            {"uid": user_id, "fd": fd, "td": td},
        )
        return [dict(row) for row in r.mappings().all()]


async def get_last_workout(user_id: str) -> dict | None:
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM workouts
                WHERE user_id = :uid AND finished_at IS NOT NULL
                ORDER BY workout_date DESC, finished_at DESC LIMIT 1
            """),
            {"uid": user_id},
        )
        row = r.mappings().first()
        return dict(row) if row else None


async def get_active_workout(user_id: str) -> dict | None:
    """Find unfinished workout started in the last 12 hours."""
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM workouts
                WHERE user_id = :uid
                  AND finished_at IS NULL
                  AND started_at >= now() - interval '12 hours'
                ORDER BY started_at DESC LIMIT 1
            """),
            {"uid": user_id},
        )
        row = r.mappings().first()
        return dict(row) if row else None


# ─────────────────────────── exercise_sets ─────────────────────────────────

async def add_set(
    workout_id: int,
    exercise_name: str,
    *,
    weight_kg: float | None = None,
    reps: int | None = None,
    reps_text: str | None = None,
    duration_seconds: int | None = None,
    superset_group: str | None = None,
    is_warmup: bool = False,
    is_failure: bool = False,
    notes: str | None = None,
) -> int:
    async with get_session() as s:
        cnt = await s.execute(
            text("""
                SELECT COALESCE(MAX(set_number), 0) + 1
                FROM exercise_sets WHERE workout_id = :wid AND exercise_name = :ex
            """),
            {"wid": workout_id, "ex": exercise_name},
        )
        set_num = cnt.scalar_one()
        r = await s.execute(
            text("""
                INSERT INTO exercise_sets
                    (workout_id, exercise_name, set_number,
                     weight_kg, reps, reps_text, duration_seconds,
                     superset_group, is_warmup, is_failure, notes)
                VALUES (:wid,:ex,:sn,:w,:r,:rt,:dur,:sg,:iw,:if_,:notes)
                RETURNING id
            """),
            {
                "wid": workout_id, "ex": exercise_name, "sn": set_num,
                "w": weight_kg, "r": reps, "rt": reps_text, "dur": duration_seconds,
                "sg": superset_group, "iw": is_warmup, "if_": is_failure, "notes": notes,
            },
        )
        return r.scalar_one()


async def update_set(set_id: int, **kwargs: Any) -> None:
    allowed = {"weight_kg", "reps", "reps_text", "duration_seconds",
               "exercise_name", "is_warmup", "is_failure", "notes", "superset_group"}
    parts, params = [], {"id": set_id}
    for k, v in kwargs.items():
        if k in allowed:
            parts.append(f"{k} = :{k}"); params[k] = v
    if not parts:
        return
    async with get_session() as s:
        await s.execute(text(f"UPDATE exercise_sets SET {', '.join(parts)} WHERE id = :id"), params)


async def delete_set(set_id: int) -> None:
    async with get_session() as s:
        await s.execute(text("DELETE FROM exercise_sets WHERE id = :id"), {"id": set_id})


async def get_workout_sets(workout_id: int) -> list[dict]:
    async with get_session() as s:
        r = await s.execute(
            text("SELECT * FROM exercise_sets WHERE workout_id = :wid ORDER BY id ASC"),
            {"wid": workout_id},
        )
        return [dict(row) for row in r.mappings().all()]


async def get_last_set(workout_id: int) -> dict | None:
    async with get_session() as s:
        r = await s.execute(
            text("SELECT * FROM exercise_sets WHERE workout_id = :wid ORDER BY id DESC LIMIT 1"),
            {"wid": workout_id},
        )
        row = r.mappings().first()
        return dict(row) if row else None
