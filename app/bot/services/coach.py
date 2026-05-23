"""
Post-workout AI coach.

Computes dry, deterministic numeric metrics from the user's history
(local DB only) and asks Claude Opus to phrase them into a short 3–5
line summary in Russian.

  facts → Opus formatter → text

No technique advice (the model never sees the user train), only:
  - tonnage of this session
  - tonnage delta vs last session with same focus
  - number of sessions with this focus in the past 7 / 30 days
  - per-exercise progression vs previous attempt (top working weight × reps)
  - any user-supplied workout/set notes get fed in so Opus can echo them
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import anthropic
from sqlalchemy import text

from app.config import settings
from app.db.engine import get_session

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


# ─────────────────────────── metrics ─────────────────────────────────────────

def _set_tonnage(s: dict) -> float:
    """weight × reps per set; sets with no weight or no reps → 0."""
    w = s.get("weight_kg")
    r = s.get("reps")
    if w is None or r is None:
        return 0.0
    return float(w) * float(r)


def _workout_tonnage(sets: list[dict]) -> float:
    return sum(_set_tonnage(s) for s in sets)


def _top_working_set(sets_of_one_exercise: list[dict]) -> dict | None:
    """The heaviest non-warmup set with reps recorded."""
    candidates = [s for s in sets_of_one_exercise
                  if not s.get("is_warmup") and s.get("weight_kg") and s.get("reps")]
    if not candidates:
        return None
    candidates.sort(key=lambda s: (float(s["weight_kg"]), int(s["reps"])), reverse=True)
    return candidates[0]


async def _load_workouts_with_sets(uid: str, from_date: date, to_date: date) -> list[tuple[dict, list[dict]]]:
    """Reads workouts in [from_date, to_date] inclusive with their sets, ordered by date asc."""
    async with get_session() as s:
        wr = await s.execute(
            text("""
                SELECT * FROM workouts
                WHERE user_id = :uid
                  AND workout_date BETWEEN :fd AND :td
                  AND finished_at IS NOT NULL
                ORDER BY workout_date ASC, started_at ASC
            """),
            {"uid": uid, "fd": from_date, "td": to_date},
        )
        workouts = [dict(r) for r in wr.mappings().all()]
        result = []
        for w in workouts:
            sr = await s.execute(
                text("SELECT * FROM exercise_sets WHERE workout_id = :wid ORDER BY id ASC"),
                {"wid": w["id"]},
            )
            sets = [dict(r) for r in sr.mappings().all()]
            result.append((w, sets))
        return result


# ───────────────────────────── facts builder ─────────────────────────────────

async def build_coach_facts(user_id: str, workout_id: int) -> dict:
    """Returns a dict with numeric facts about the workout vs history.

    Schema:
      {
        "focus": str | None,
        "this_workout": {
            "tonnage_kg": float,
            "sets": int,
            "exercises": [{"name": str, "sets": int, "top": {"w": float, "reps": int}}, ...],
            "workout_notes": str | None,
            "set_notes_sample": list[str],   # up to 3 unique notes from sets
        },
        "history": {
            "same_focus_count_7d": int,
            "same_focus_count_30d": int,
            "previous_same_focus": {            # last completed workout with same focus_label
                "date": str (ISO),
                "tonnage_kg": float,
            } | None,
            "delta_pct": float | None,
        },
        "exercise_progression": [
            {"name": str, "now": "WxR", "prev": "WxR" | None, "delta_pct": float | None}
        ],
      }
    """
    async with get_session() as s:
        wr = await s.execute(text("SELECT * FROM workouts WHERE id = :id"), {"id": workout_id})
        w_row = wr.mappings().first()
        if not w_row:
            return {}
        workout = dict(w_row)

        sr = await s.execute(
            text("SELECT * FROM exercise_sets WHERE workout_id = :wid ORDER BY id ASC"),
            {"wid": workout_id},
        )
        sets = [dict(r) for r in sr.mappings().all()]

    focus = (workout.get("focus_label") or "").strip()
    today = workout.get("workout_date") or date.today()
    if isinstance(today, str):
        today = date.fromisoformat(today)

    # By exercise
    by_ex: dict[str, list[dict]] = {}
    for st in sets:
        by_ex.setdefault(st["exercise_name"], []).append(st)

    this_exercises = []
    for name, ex_sets in by_ex.items():
        top = _top_working_set(ex_sets)
        item = {
            "name": name,
            "sets": len([x for x in ex_sets if not x.get("is_warmup")]),
            "top": None,
        }
        if top:
            item["top"] = {"w": float(top["weight_kg"]), "reps": int(top["reps"])}
        this_exercises.append(item)

    # Notes
    workout_notes = workout.get("notes")
    set_notes = [s.get("notes") for s in sets if s.get("notes")]
    seen = set()
    uniq_notes = []
    for n in set_notes:
        if n and n not in seen:
            seen.add(n)
            uniq_notes.append(n)
        if len(uniq_notes) >= 3:
            break

    # History — same focus
    same_focus_30d = await _load_workouts_with_sets(user_id, today - timedelta(days=30), today)
    # exclude current
    same_focus_30d = [
        (w, s) for w, s in same_focus_30d
        if w["id"] != workout_id and (w.get("focus_label") or "").strip().lower() == focus.lower()
    ]

    count_7d = sum(
        1 for w, _ in same_focus_30d
        if (today - (w["workout_date"] if isinstance(w["workout_date"], date)
                     else date.fromisoformat(str(w["workout_date"])))).days <= 7
    )
    count_30d = len(same_focus_30d)

    prev_same = None
    delta_pct = None
    if same_focus_30d:
        prev_w, prev_sets = same_focus_30d[-1]  # last one before current
        prev_tonnage = _workout_tonnage(prev_sets)
        prev_date_val = prev_w["workout_date"]
        if not isinstance(prev_date_val, date):
            prev_date_val = date.fromisoformat(str(prev_date_val))
        prev_same = {
            "date": prev_date_val.isoformat(),
            "tonnage_kg": round(prev_tonnage, 1),
        }
        this_tonnage = _workout_tonnage(sets)
        if prev_tonnage > 0:
            delta_pct = round((this_tonnage - prev_tonnage) / prev_tonnage * 100, 1)

    # Per-exercise progression vs the most recent previous appearance (any focus)
    ex_progression = []
    for name, ex_sets in by_ex.items():
        top_now = _top_working_set(ex_sets)
        if not top_now:
            continue
        # find latest previous appearance
        prev_top = await _previous_top_for_exercise(user_id, name, before=today, current_workout_id=workout_id)
        item = {
            "name": name,
            "now": f"{float(top_now['weight_kg']):g}×{int(top_now['reps'])}",
            "prev": None,
            "delta_pct": None,
        }
        if prev_top:
            item["prev"] = f"{float(prev_top['weight_kg']):g}×{int(prev_top['reps'])}"
            now_load = float(top_now['weight_kg']) * float(top_now['reps'])
            prev_load = float(prev_top['weight_kg']) * float(prev_top['reps'])
            if prev_load > 0:
                item["delta_pct"] = round((now_load - prev_load) / prev_load * 100, 1)
        ex_progression.append(item)

    return {
        "focus": focus,
        "this_workout": {
            "tonnage_kg": round(_workout_tonnage(sets), 1),
            "sets": len(sets),
            "exercises": this_exercises,
            "workout_notes": workout_notes,
            "set_notes_sample": uniq_notes,
        },
        "history": {
            "same_focus_count_7d": count_7d,
            "same_focus_count_30d": count_30d,
            "previous_same_focus": prev_same,
            "delta_pct": delta_pct,
        },
        "exercise_progression": ex_progression,
    }


async def _previous_top_for_exercise(
    user_id: str, exercise_name: str, before: date, current_workout_id: int,
) -> dict | None:
    """Most recent top working set of this exercise before `before`, by this user."""
    async with get_session() as s:
        r = await s.execute(
            text("""
                SELECT es.*, w.workout_date
                FROM exercise_sets es
                JOIN workouts w ON w.id = es.workout_id
                WHERE w.user_id = :uid
                  AND es.exercise_name = :name
                  AND w.id <> :curid
                  AND w.workout_date <= :before
                  AND es.is_warmup = FALSE
                  AND es.weight_kg IS NOT NULL
                  AND es.reps IS NOT NULL
                ORDER BY w.workout_date DESC, es.weight_kg DESC, es.reps DESC
                LIMIT 1
            """),
            {"uid": user_id, "name": exercise_name, "curid": current_workout_id, "before": before},
        )
        row = r.mappings().first()
        return dict(row) if row else None


# ─────────────────────── Opus phrasing ───────────────────────────────────────

_COACH_SYSTEM = """\
Ты тренер-статистик. Получаешь сухие факты о завершённой тренировке и истории, \
формулируешь короткий отчёт (3-5 строк, разговорный русский).

Правила:
- ТОЛЬКО факты которые я дал. Никаких советов по технике, питанию, мотивационных фраз
- Кратко, тезисно, цифры с единицами
- Если есть прогресс — отмечай. Если регресс — констатируй, можно сказать "повтори этот вес ещё раз"
- Если тренировал ту же группу 2+ раз за неделю — упоминай ("второй раз груди за неделю")
- Если delta_pct по тренировке = null или нет previous_same_focus — не выдумывай
- Используй workout_notes и set_notes_sample когда они есть (это пользователь сам отметил, важно)
- Не пиши заголовков типа "Отчёт:". Сразу по делу. Без эмодзи в первой строке
- Кратко (не более 5 строк, идеально 3)
"""


async def opus_coach_summary(facts: dict) -> str:
    """Ask Opus to phrase the facts. Returns plain text (no HTML)."""
    if not facts:
        return "Нет данных для разбора."
    try:
        resp = await _anthropic.messages.create(
            model="claude-opus-4-7",
            max_tokens=400,
            system=_COACH_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.error("opus_coach_summary error: %s", exc)
        # Deterministic fallback when Opus is unavailable
        return _fallback_summary(facts)


def _fallback_summary(facts: dict) -> str:
    f = facts.get("focus") or "тренировка"
    tw = facts.get("this_workout", {})
    h = facts.get("history", {})
    parts = [f"{f.capitalize()}: тоннаж {tw.get('tonnage_kg', 0):g} кг, подходов {tw.get('sets', 0)}."]
    if h.get("delta_pct") is not None:
        sign = "+" if h["delta_pct"] >= 0 else ""
        prev = h.get("previous_same_focus") or {}
        parts.append(f"К прошлой ({prev.get('date','?')}, {prev.get('tonnage_kg',0):g} кг): {sign}{h['delta_pct']}%.")
    if h.get("same_focus_count_7d", 0) >= 2:
        parts.append(f"Это {h['same_focus_count_7d']+1}-я тренировка этой группы за неделю.")
    return " ".join(parts)
