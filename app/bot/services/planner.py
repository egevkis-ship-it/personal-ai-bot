"""
AI workout planner — generates a one-day or one-week plan based on the
user's recent history. Uses Claude Opus.

Inputs collected from DB:
  - last 14 days of completed workouts (focus, exercises, sets, notes)
  - any planned workouts in the upcoming week (to avoid duplicates)

Output:
  - PlannedDay (single day) or list[PlannedDay] (week, Mon-Sun)
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import anthropic
from sqlalchemy import text

from app.config import settings
from app.bot.services.ai_parser import PlannedDay, PlannedExercise
from app.db.engine import get_session

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def _gather_recent_history(user_id: str, days_back: int = 14) -> list[dict]:
    """Returns compact dict per workout for AI consumption."""
    from app.bot.services.tz import today as _today
    today = await _today(user_id)
    from_d = today - timedelta(days=days_back)
    async with get_session() as s:
        wr = await s.execute(
            text("""
                SELECT * FROM workouts
                WHERE user_id = :uid AND workout_date >= :fd AND finished_at IS NOT NULL
                ORDER BY workout_date ASC
            """),
            {"uid": user_id, "fd": from_d},
        )
        workouts = [dict(r) for r in wr.mappings().all()]
        out = []
        for w in workouts:
            sr = await s.execute(
                text("SELECT * FROM exercise_sets WHERE workout_id = :wid ORDER BY id ASC"),
                {"wid": w["id"]},
            )
            sets = [dict(r) for r in sr.mappings().all()]
            # Group by exercise compactly
            by_ex = {}
            for st in sets:
                name = st["exercise_name"]
                by_ex.setdefault(name, [])
                if st.get("weight_kg") and st.get("reps"):
                    by_ex[name].append({
                        "w": float(st["weight_kg"]),
                        "r": int(st["reps"]),
                        "warmup": bool(st.get("is_warmup")),
                    })
            d_val = w["workout_date"]
            if not isinstance(d_val, date):
                d_val = date.fromisoformat(str(d_val))
            out.append({
                "date": d_val.isoformat(),
                "weekday": d_val.strftime("%A"),
                "focus": w.get("focus_label"),
                "notes": w.get("notes"),
                "exercises": {k: v for k, v in by_ex.items() if v},
            })
        return out


async def _gather_upcoming_plans(user_id: str, from_d: date, to_d: date) -> list[dict]:
    """Compact list of already-planned workouts in window."""
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT * FROM planned_workouts
                WHERE user_id = :uid AND planned_date BETWEEN :fd AND :td
                  AND status = 'planned'
                ORDER BY planned_date ASC
            """),
            {"uid": user_id, "fd": from_d, "td": to_d},
        )
        rows = [dict(x) for x in r.mappings().all()]
    out = []
    for row in rows:
        d_val = row["planned_date"]
        if not isinstance(d_val, date):
            d_val = date.fromisoformat(str(d_val))
        out.append({
            "date": d_val.isoformat(),
            "focus": row.get("focus_label"),
        })
    return out


_PLANNER_DAY_SYSTEM = """\
Ты тренер. Получаешь историю тренировок пользователя за последние 14 дней и список уже \
запланированных тренировок на ближайшее время. Должен предложить ОДНУ тренировку \
на указанную дату, ориентируясь на стиль того, как пользователь тренируется (что было \
последние 1-2 недели), и избегая повтора группы, которую он уже тренировал на этой неделе.

Верни JSON:
{
  "day_label": "Понедельник, 25 мая",
  "focus_label": "Грудь / Трицепс",
  "notes": null,
  "exercises": [
    {"name":"Жим штанги лёжа","target_sets":4,"target_reps_min":8,"target_reps_max":10,"target_weight":80.0,"reps_text":null,"notes":null,"superset_group":null}
  ]
}

Правила:
- 5-7 упражнений
- target_weight = последний рабочий вес из истории если есть, иначе null
- Названия упражнений как у пользователя
- Не повторяй фокус который был 0-2 дня назад
- Только JSON
"""


_PLANNER_WEEK_SYSTEM = """\
Ты тренер. На основе истории за 14 дней предложи план на 7 дней (Пн-Вс) начиная с указанной даты.

JSON:
{"days":[
  {"day_label":"Понедельник","focus_label":"Грудь","notes":null,"exercises":[...]},
  ...
]}

Правила:
- 4-5 рабочих дней + 2-3 дня отдыха (exercises:[])
- Структура и стиль как в истории
- Веса = последние рабочие из истории
- Только JSON
"""


def _parse_planned_day_json(data: dict) -> PlannedDay:
    exs = [
        PlannedExercise(
            name=e["name"],
            target_sets=e.get("target_sets"),
            target_reps_min=e.get("target_reps_min"),
            target_reps_max=e.get("target_reps_max"),
            target_weight=e.get("target_weight"),
            reps_text=e.get("reps_text"),
            notes=e.get("notes"),
            superset_group=e.get("superset_group"),
        )
        for e in data.get("exercises", [])
    ]
    return PlannedDay(
        day_label=data.get("day_label", ""),
        focus_label=data.get("focus_label"),
        notes=data.get("notes"),
        exercises=exs,
    )


def _strip_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return raw.strip()


async def generate_day_plan(user_id: str, target_date: date) -> PlannedDay | None:
    from app.bot.services.tz import today as _today
    _t = await _today(user_id)
    history = await _gather_recent_history(user_id)
    upcoming = await _gather_upcoming_plans(
        user_id, _t, _t + timedelta(days=14)
    )
    payload = {
        "target_date": target_date.isoformat(),
        "target_weekday": target_date.strftime("%A"),
        "recent_history": history,
        "upcoming_plans": upcoming,
    }
    try:
        resp = await _anthropic.messages.create(
            model="claude-opus-4-8",
            max_tokens=1500,
            system=_PLANNER_DAY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        raw = _strip_fence(resp.content[0].text)
        data = json.loads(raw)
        return _parse_planned_day_json(data)
    except Exception as exc:
        log.error("generate_day_plan error: %s", exc, exc_info=True)
        return None


async def generate_week_plan(user_id: str, start_date: date) -> list[PlannedDay]:
    history = await _gather_recent_history(user_id)
    upcoming = await _gather_upcoming_plans(
        user_id, start_date, start_date + timedelta(days=7)
    )
    payload = {
        "start_date": start_date.isoformat(),
        "end_date": (start_date + timedelta(days=6)).isoformat(),
        "recent_history": history,
        "upcoming_plans": upcoming,
    }
    try:
        resp = await _anthropic.messages.create(
            model="claude-opus-4-8",
            max_tokens=3500,
            system=_PLANNER_WEEK_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        raw = _strip_fence(resp.content[0].text)
        data = json.loads(raw)
        return [_parse_planned_day_json(d) for d in data.get("days", [])]
    except Exception as exc:
        log.error("generate_week_plan error: %s", exc, exc_info=True)
        return []
