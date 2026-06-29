"""
Flagship AI coach — "build next week" (VISION.md).

build_week_brief(uid): assembles the user's training dossier DIRECTLY from the DB
(owner-scoped) — recent workouts + sets + notes, per-exercise progress/PR, weekly
volume, body-weight trend. No manual export.

generate_week(brief, recovery_mode, answers): a deep-analysis Anthropic call that
returns a structured week + a short rationale + flags. The model gives TRAINING
guidance only — no medical/pharmacological advice (see the system prompt).
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text

from app.bot.services.ai_parser import PARSER_MODEL, _anthropic
from app.db.engine import get_session

log = logging.getLogger(__name__)


class CoachError(Exception):
    """The coach AI call itself failed (model/network/auth) — surfaced as a 502,
    distinct from a model that replied with unparseable JSON."""


async def build_week_brief(uid: str) -> dict:
    """Owner-scoped training dossier for the coach, straight from the DB."""
    async with get_session() as s:
        # Recent finished workouts (last 28 days) with their working+warmup sets.
        wo_rows = (await s.execute(text("""
            SELECT w.id, w.workout_date, w.focus_label, w.notes
            FROM workouts w
            WHERE w.user_id = :u AND w.finished_at IS NOT NULL
              AND w.workout_date >= (CURRENT_DATE - INTERVAL '28 days')
            ORDER BY w.workout_date DESC LIMIT 20
        """), {"u": uid})).mappings().all()
        wo_ids = [r["id"] for r in wo_rows]
        sets_by_wo: dict = {}
        if wo_ids:
            set_rows = (await s.execute(text("""
                SELECT workout_id, exercise_name, set_number, weight_kg, reps,
                       reps_text, duration_seconds, is_warmup, is_failure, notes
                FROM exercise_sets WHERE workout_id = ANY(:ids)
                ORDER BY workout_id, set_number
            """), {"ids": wo_ids})).mappings().all()
            for s_ in set_rows:
                sets_by_wo.setdefault(s_["workout_id"], []).append(s_)

        # Per-exercise summary: best ever weight, most-recent top weight, # sessions.
        ex_rows = (await s.execute(text("""
            SELECT es.exercise_name AS name,
                   MAX(es.weight_kg) AS best_weight,
                   COUNT(DISTINCT w.workout_date) AS sessions,
                   MAX(w.workout_date) AS last_done
            FROM exercise_sets es JOIN workouts w ON w.id = es.workout_id
            WHERE w.user_id = :u AND w.finished_at IS NOT NULL AND es.is_warmup = false
              AND es.weight_kg IS NOT NULL
            GROUP BY es.exercise_name ORDER BY last_done DESC LIMIT 40
        """), {"u": uid})).mappings().all()

        # Weekly volume (last 8 weeks): tonnage + workout count.
        vol_rows = (await s.execute(text("""
            SELECT date_trunc('week', w.workout_date)::date AS week,
                   COUNT(DISTINCT w.id) AS workouts,
                   COALESCE(ROUND(SUM(es.weight_kg * es.reps) FILTER (WHERE es.is_warmup = false))::int, 0) AS tonnage
            FROM workouts w LEFT JOIN exercise_sets es ON es.workout_id = w.id
            WHERE w.user_id = :u AND w.finished_at IS NOT NULL
              AND w.workout_date >= (CURRENT_DATE - INTERVAL '56 days')
            GROUP BY week ORDER BY week DESC
        """), {"u": uid})).mappings().all()

        wt_rows = (await s.execute(text("""
            SELECT taken_on, weight_kg FROM body_measurements
            WHERE user_id = :u AND weight_kg IS NOT NULL ORDER BY taken_on DESC LIMIT 10
        """), {"u": uid})).mappings().all()

    def _f(v):
        return float(v) if v is not None else None

    recent_workouts = []
    notes: list[str] = []
    for w in wo_rows:
        if w.get("notes"):
            notes.append(f"{w['workout_date']}: {w['notes']}")
        exs: dict = {}
        for st in sets_by_wo.get(w["id"], []):
            e = exs.setdefault(st["exercise_name"], [])
            e.append({"weight": _f(st["weight_kg"]), "reps": st["reps"],
                      "warmup": st["is_warmup"], "failure": st["is_failure"],
                      "reps_text": st["reps_text"], "duration_s": st["duration_seconds"]})
            if st.get("notes"):
                notes.append(f"{w['workout_date']} {st['exercise_name']}: {st['notes']}")
        recent_workouts.append({
            "date": w["workout_date"].isoformat(), "focus": w["focus_label"], "notes": w["notes"],
            "exercises": [{"name": k, "sets": v} for k, v in exs.items()],
        })

    return {
        "recent_workouts": recent_workouts,
        "exercise_summary": [{"name": r["name"], "best_weight": _f(r["best_weight"]),
                              "sessions": r["sessions"], "last_done": r["last_done"].isoformat()}
                             for r in ex_rows],
        "weekly_volume": [{"week": r["week"].isoformat(), "workouts": r["workouts"], "tonnage": r["tonnage"]}
                          for r in vol_rows],
        "weight_trend": [{"date": r["taken_on"].isoformat(), "weight_kg": _f(r["weight_kg"])}
                         for r in reversed(wt_rows)],
        "recent_notes": notes[:40],
    }


_WEEK_SYSTEM = """\
Ты опытный тренер-наставник по силовым тренировкам. На входе — досье пользователя \
ПРЯМО из его базы (последние тренировки и подходы с заметками, прогресс и рекорды \
по упражнениям, недельный объём/тоннаж, тренд веса тела) + контекст самочувствия \
(сон, энергия, стресс, травмы/боли) + заявленный режим восстановления.

Сделай КОМПЛЕКСНЫЙ разбор (а не просто «дай план»):
- где прогресс, где плато/застой по упражнениям; оцени недельный объём и частоту \
проработки групп, риск перетренированности/недовосстановления;
- учти заметки (RIR, откаты по весу, боли, ощущения) и тренд веса/самочувствия;
- предложи корректировки по объёму / интенсивности / частоте / выбору упражнений \
с учётом восстановления.

ПРАВИЛО ВОССТАНОВЛЕНИЯ (это чисто тренировочная подстройка нагрузки под ЗАЯВЛЕННУЮ \
пользователем восстановительную способность):
- recovery_mode = "enhanced" → программируй БОЛЬШИЙ недельный объём, более высокую \
частоту проработки групп, более агрессивную прогрессию и более редкие разгрузки.
- recovery_mode = "natural" → консервативнее: умеренный объём, аккуратная прогрессия, \
своевременные разгрузки.
- В ЛЮБОМ режиме следи по данным/заметкам за реальными признаками перетрена, боли и \
травм — снижай нагрузку на затронутое; повышенное восстановление не отменяет здравый \
риск-менеджмент.

ГРАНИЦА (строго): ты даёшь ТОЛЬКО тренировочные рекомендации (объём/интенсивность/\
частота/выбор упражнений/восстановление). НИКАКИХ медицинских и фармакологических \
советов: не обсуждай выбор препаратов, дозировки, протоколы, ПКТ, управление здоровьем \
на курсе, анализы как медрекомендацию. Если тема уходит в препараты/здоровье — коротко \
отошли к специалисту. Это НЕ медицинская рекомендация.

Сгенерируй план на неделю. Ответ — СТРОГО JSON по схеме, без markdown и текста вокруг:
{
  "days": [
    {"weekday": 0, "focus_label": "Грудь / Трицепс", "notes": "...",
     "exercises": [
       {"name": "Жим штанги лёжа", "target_sets": 4, "target_reps_min": 6,
        "target_reps_max": 8, "target_weight": 95.0, "reps_text": null, "notes": "..."}
     ]}
  ],
  "rationale": "Короткое (2-4 предложения) объяснение почему так — опираясь на данные/заметки.",
  "flags": ["короткие флаги-предупреждения, на что обратить внимание"]
}
weekday: 0=Понедельник … 6=Воскресенье. День отдыха — НЕ включай (или exercises:[]). \
Вес ставь реалистично от текущих рабочих. Если данных мало — работай по тому, что есть, \
и скажи об этом в rationale."""


def _extract_json(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in s else s
    a, b = s.find("{"), s.rfind("}")
    return s[a:b + 1] if a >= 0 and b > a else s


async def generate_week(brief: dict, recovery_mode: str, answers: dict | None) -> dict:
    """Deep-analysis week generation. Returns {days, rationale, flags}. Raises
    CoachError if the Anthropic call fails (→ 502); returns a soft error dict if
    the reply isn't valid JSON."""
    payload = json.dumps({
        "today_is_monday_of_target_week": True,
        "recovery_mode": recovery_mode if recovery_mode in ("natural", "enhanced") else "natural",
        "survey_answers": answers or {},
        "dossier": brief,
    }, ensure_ascii=False)
    try:
        resp = await _anthropic.messages.create(
            model=PARSER_MODEL, max_tokens=8192, system=_WEEK_SYSTEM,
            messages=[{"role": "user", "content": payload}])
    except Exception as exc:
        log.error("generate_week: Anthropic call failed: %s", exc, exc_info=True)
        raise CoachError(str(exc)) from exc
    raw = resp.content[0].text if resp.content else "{}"
    try:
        data = json.loads(_extract_json(raw))
    except Exception as exc:
        log.warning("generate_week: bad JSON: %s | raw[:200]=%r", exc, raw[:200])
        return {"days": [], "rationale": "", "flags": ["Не удалось разобрать ответ ИИ — попробуй ещё раз."]}
    return {
        "days": data.get("days", []) if isinstance(data, dict) else [],
        "rationale": (data.get("rationale") or "") if isinstance(data, dict) else "",
        "flags": data.get("flags", []) if isinstance(data, dict) else [],
    }
