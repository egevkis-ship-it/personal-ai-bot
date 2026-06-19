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


def _to_date(v: str | date) -> date:
    """asyncpg requires real date objects for DATE columns — never strings."""
    return v if isinstance(v, date) else date.fromisoformat(v)


# ─────────────────────── planned_workouts ──────────────────────────────────

async def create_planned_workout(
    user_id: str,
    planned_date: str | date,
    focus_label: str | None,
    exercises: list[dict],
) -> int:
    d = _to_date(planned_date)
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
    fd = _to_date(from_date)
    td = _to_date(to_date)
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
    from app.bot.services.tz import today as _today
    d = await _today(user_id)
    rows = await get_planned_workouts_range(user_id, d, d)
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
    d = _to_date(workout_date)
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


async def update_workout_notes(workout_id: int, notes: str) -> None:
    async with get_session() as s:
        await s.execute(
            text("UPDATE workouts SET notes = :n WHERE id = :id"),
            {"id": workout_id, "n": notes},
        )


async def update_planned_workout_notes(plan_id: int, notes: str) -> None:
    async with get_session() as s:
        await s.execute(
            text("UPDATE planned_workouts SET notes = :n, updated_at = now() WHERE id = :id"),
            {"id": plan_id, "n": notes},
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
    fd = _to_date(from_date)
    td = _to_date(to_date)
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


# ─────────────────────────── service ops ───────────────────────────────────

async def db_stats(user_id: str) -> dict:
    """Counts per table for the user (planned, workouts, measurements, photos)."""
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT
                    (SELECT COUNT(*) FROM planned_workouts WHERE user_id = :uid AND status = 'planned') AS planned_active,
                    (SELECT COUNT(*) FROM planned_workouts WHERE user_id = :uid) AS planned_total,
                    (SELECT COUNT(*) FROM workouts WHERE user_id = :uid) AS workouts_total,
                    (SELECT COUNT(*) FROM workouts WHERE user_id = :uid AND finished_at IS NOT NULL) AS workouts_finished,
                    (SELECT COUNT(*) FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
                       WHERE w.user_id = :uid) AS sets_total,
                    (SELECT COUNT(*) FROM body_measurements WHERE user_id = :uid) AS measurements_total,
                    (SELECT COUNT(*) FROM progress_photos WHERE user_id = :uid) AS photos_total,
                    (SELECT COUNT(*) FROM exercise_aliases) AS aliases_total
            """),
            {"uid": user_id},
        )
        row = r.mappings().first()
        return dict(row) if row else {}


async def wipe_planned_workouts(user_id: str) -> int:
    async with get_session() as s:
        r = await s.execute(
            text("DELETE FROM planned_workouts WHERE user_id = :uid"),
            {"uid": user_id},
        )
        return r.rowcount or 0


async def wipe_workouts(user_id: str) -> int:
    """Deletes workouts + cascades sets via FK."""
    async with get_session() as s:
        r = await s.execute(
            text("DELETE FROM workouts WHERE user_id = :uid"),
            {"uid": user_id},
        )
        return r.rowcount or 0


async def wipe_exercise_aliases() -> int:
    """Global wipe — alias cache is not per-user."""
    async with get_session() as s:
        r = await s.execute(text("DELETE FROM exercise_aliases"))
        return r.rowcount or 0


async def wipe_all_user_data(user_id: str) -> dict:
    """Wipe everything for this user. Returns counts deleted."""
    plans = await wipe_planned_workouts(user_id)
    workouts = await wipe_workouts(user_id)
    measurements = await wipe_measurements(user_id)
    photos = await wipe_photos(user_id)
    return {
        "planned": plans,
        "workouts": workouts,
        "measurements": measurements,
        "photos": photos,
    }


# ─────────────────────────── body measurements ─────────────────────────────

_M_FIELDS = (
    "weight_kg", "calf_cm", "thigh_cm", "hips_cm", "belly_cm",
    "waist_cm", "chest_cm", "arm_cm", "neck_cm",
)


async def create_measurement(
    user_id: str,
    taken_on: str | date,
    values: dict,
    notes: str | None = None,
) -> int:
    d = _to_date(taken_on)
    cols = ["user_id", "taken_on", "notes"]
    params: dict = {"uid": user_id, "d": d, "notes": notes}
    placeholders = [":uid", ":d", ":notes"]
    for f in _M_FIELDS:
        if f in values and values[f] is not None:
            cols.append(f); placeholders.append(f":{f}"); params[f] = values[f]
    sql = (
        f"INSERT INTO body_measurements ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)}) RETURNING id"
    )
    async with get_session() as s:
        r = await s.execute(text(sql), params)
        return r.scalar_one()


async def get_measurements(user_id: str, limit: int = 30) -> list[dict]:
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM body_measurements
                WHERE user_id = :uid
                ORDER BY taken_on DESC, id DESC
                LIMIT :lim
            """),
            {"uid": user_id, "lim": limit},
        )
        return [dict(x) for x in r.mappings().all()]


async def get_last_measurement(user_id: str) -> dict | None:
    rows = await get_measurements(user_id, limit=1)
    return rows[0] if rows else None


async def delete_measurement(measurement_id: int) -> None:
    async with get_session() as s:
        await s.execute(text("DELETE FROM body_measurements WHERE id = :id"), {"id": measurement_id})


async def wipe_measurements(user_id: str) -> int:
    async with get_session() as s:
        r = await s.execute(
            text("DELETE FROM body_measurements WHERE user_id = :uid"),
            {"uid": user_id},
        )
        return r.rowcount or 0


# ─────────────────────────── progress photos ──────────────────────────────

async def create_photo(
    user_id: str,
    taken_on: str | date,
    telegram_file_id: str,
    telegram_file_unique_id: str | None = None,
    ai_description: str | None = None,
    notes: str | None = None,
) -> int:
    d = _to_date(taken_on)
    async with get_session() as s:
        r = await s.execute(
            text("""
                INSERT INTO progress_photos
                    (user_id, taken_on, telegram_file_id, telegram_file_unique_id,
                     ai_description, notes)
                VALUES (:uid, :d, :fid, :fuid, :desc, :notes)
                RETURNING id
            """),
            {
                "uid": user_id, "d": d, "fid": telegram_file_id,
                "fuid": telegram_file_unique_id, "desc": ai_description, "notes": notes,
            },
        )
        return r.scalar_one()


async def get_photos(user_id: str, limit: int = 30) -> list[dict]:
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM progress_photos
                WHERE user_id = :uid
                ORDER BY taken_on DESC, id DESC
                LIMIT :lim
            """),
            {"uid": user_id, "lim": limit},
        )
        return [dict(x) for x in r.mappings().all()]


async def get_photo(photo_id: int) -> dict | None:
    async with get_session() as s:
        r = await s.execute(text("SELECT * FROM progress_photos WHERE id = :id"), {"id": photo_id})
        row = r.mappings().first()
        return dict(row) if row else None


async def update_photo_notes(photo_id: int, notes: str) -> None:
    async with get_session() as s:
        await s.execute(
            text("UPDATE progress_photos SET notes = :n WHERE id = :id"),
            {"id": photo_id, "n": notes},
        )


async def get_photo_series(user_id: str, limit: int = 30) -> list[dict]:
    """Group photos by series_id, return one row per series ordered newest-first.

    Each returned dict contains:
      - series_id (may equal the photo id when series_id is NULL)
      - taken_on
      - ai_description, ai_description_short, notes (taken from any photo in
        the series — they are duplicated by design)
      - photo_count
      - photo_ids (list of int in insertion order)
      - first_file_id (first telegram_file_id for preview)
    """
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM progress_photos
                WHERE user_id = :uid
                ORDER BY taken_on DESC, id DESC
            """),
            {"uid": user_id},
        )
        rows = [dict(x) for x in r.mappings().all()]
    series: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        key = row.get("series_id") or f"_solo_{row['id']}"
        if key not in series:
            series[key] = {
                "series_id": key,
                "taken_on": row["taken_on"],
                "ai_description": row.get("ai_description"),
                "ai_description_short": row.get("ai_description_short"),
                "notes": row.get("notes"),
                "photo_ids": [],
                "telegram_file_ids": [],
                "first_file_id": row["telegram_file_id"],
            }
            order.append(key)
        series[key]["photo_ids"].append(row["id"])
        series[key]["telegram_file_ids"].append(row["telegram_file_id"])
        # If this row has notes but series doesn't yet, take them
        if not series[key]["notes"] and row.get("notes"):
            series[key]["notes"] = row["notes"]
        if not series[key]["ai_description_short"] and row.get("ai_description_short"):
            series[key]["ai_description_short"] = row["ai_description_short"]
    out = [series[k] for k in order[:limit]]
    for s_ in out:
        s_["photo_count"] = len(s_["photo_ids"])
    return out


async def delete_photo_series(user_id: str, series_id: str) -> int:
    async with get_session() as s:
        if series_id.startswith("_solo_"):
            pid = int(series_id.split("_", 2)[2])
            r = await s.execute(
                text("DELETE FROM progress_photos WHERE id = :id AND user_id = :uid"),
                {"id": pid, "uid": user_id},
            )
        else:
            r = await s.execute(
                text("DELETE FROM progress_photos WHERE series_id = :sid AND user_id = :uid"),
                {"sid": series_id, "uid": user_id},
            )
        return r.rowcount or 0


async def delete_photo(photo_id: int) -> None:
    async with get_session() as s:
        await s.execute(text("DELETE FROM progress_photos WHERE id = :id"), {"id": photo_id})


async def wipe_photos(user_id: str) -> int:
    async with get_session() as s:
        r = await s.execute(
            text("DELETE FROM progress_photos WHERE user_id = :uid"),
            {"uid": user_id},
        )
        return r.rowcount or 0
