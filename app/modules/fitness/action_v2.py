from __future__ import annotations
import json
import re
from datetime import date, datetime, timezone

from app.ai import client
from app.db import (
    create_fitness_pending_decision,
    get_latest_fitness_pending_decision,
    resolve_fitness_pending_decision,
    get_today_planned_workout,
    get_planned_workouts_in_period,
    get_latest_planned_workout_template_by_focus,
    replace_planned_workout,
    save_training_plan,
    save_fitness_workout_session_v2,
    append_fitness_workout_sets_v2,
    update_fitness_pending_decision_context,
    delete_last_fitness_set_v2,
    update_last_fitness_set_v2,
)
from app.modules.fitness.exercise_history import handle_exercise_history_request
from app.modules.fitness.formatter import (
    format_planned_workout,
    format_period_plan,
    format_human_date,
)
from app.modules.fitness.utils import (
    week_bounds,
    next_week_bounds,
    month_bounds,
    next_month_bounds,
)


def _safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def _active_session_context_from_pending(pending: dict | None) -> dict | None:
    if not pending:
        return None
    if pending.get("decision_type") != "active_workout_session":
        return None
    return pending.get("context_json") or {}


def _normalize_exercises(raw_exercises: list[dict] | None) -> list[dict]:
    result = []
    for i, ex in enumerate(raw_exercises or [], start=1):
        name = (
            ex.get("exercise_name")
            or ex.get("name")
            or ex.get("title")
            or ex.get("exercise")
        )
        if not name:
            continue

        sets = ex.get("target_sets") or ex.get("sets")
        reps_text = ex.get("target_reps_text") or ex.get("reps_text")
        reps_min = ex.get("target_reps_min") or ex.get("reps_min")
        reps_max = ex.get("target_reps_max") or ex.get("reps_max")
        weight = ex.get("target_weight_kg") or ex.get("weight_kg")

        result.append({
            "exercise_order": ex.get("exercise_order") or i,
            "exercise_name": name,
            "target_sets": sets,
            "target_reps_min": reps_min,
            "target_reps_max": reps_max,
            "target_reps_text": reps_text,
            "target_weight_kg": weight,
            "notes": ex.get("notes"),
        })
    return result


def _normalize_logged_exercises(raw_exercises: list[dict] | None) -> list[dict]:
    result = []
    for ex in raw_exercises or []:
        name = (
            ex.get("exercise_name")
            or ex.get("name")
            or ex.get("title")
            or ex.get("exercise")
        )
        if not name:
            continue

        sets = []
        for i, s in enumerate(ex.get("sets") or [], start=1):
            reps = s.get("reps")
            weight = s.get("weight_kg")
            if reps is None and weight is None:
                continue
            sets.append({
                "set_number": s.get("set_number") or i,
                "weight_kg": weight,
                "reps": reps,
                "rpe": s.get("rpe"),
                "notes": s.get("notes"),
            })

        if sets:
            result.append({
                "exercise_name": name,
                "sets": sets,
                "notes": ex.get("notes"),
            })

    return result


def parse_fitness_action_v2(text: str, active_session: dict | None = None) -> dict:
    today = date.today().isoformat()
    current_week_start, current_week_end = week_bounds()
    next_week_start, next_week_end = next_week_bounds()
    month_start, month_end = month_bounds()
    next_month_start, next_month_end = next_month_bounds()

    system_prompt = f"""
Ты главный parser фитнес-ассистента Егора.

Сегодня: {today}
Текущая неделя: {current_week_start} — {current_week_end}
Следующая неделя: {next_week_start} — {next_week_end}
Текущий месяц: {month_start} — {month_end}
Следующий месяц: {next_month_start} — {next_month_end}

Активная тренировочная сессия, если есть:
{json.dumps(active_session or {}, ensure_ascii=False)}

Твоя задача — вернуть СТРОГО JSON, без markdown.

Схема:
{{
  "action": "show_today_workout | show_week_plan | show_next_week_plan | show_month_plan | show_next_month_plan | show_workout_on_date | replace_today_workout | add_custom_workout | log_workout_sets | continue_current_exercise | correct_previous_action | delete_last_set | dangerous_delete | unknown | clarify",
  "confidence": 0.0,
  "date": null,
  "weekday": null,
  "period": {{
    "start_date": null,
    "end_date": null,
    "period_type": null
  }},
  "target": {{
    "focus": null,
    "focus_label": null,
    "exercise_name": null,
    "set_number": null
  }},
  "workout": {{
    "title": null,
    "focus": null,
    "focus_label": null,
    "notes": null,
    "exercises": [
      {{
        "exercise_name": null,
        "target_sets": null,
        "target_reps_min": null,
        "target_reps_max": null,
        "target_reps_text": null,
        "target_weight_kg": null,
        "notes": null
      }}
    ]
  }},
  "logged_exercises": [
    {{
      "exercise_name": null,
      "sets": [
        {{
          "set_number": null,
          "weight_kg": null,
          "reps": null,
          "rpe": null,
          "notes": null
        }}
      ],
      "notes": null
    }}
  ],
  "correction": {{
    "field": null,
    "old_value": null,
    "new_value": null
  }},
  "needs_confirmation": false,
  "summary": ""
}}

Правила:

1. Запросы просмотра:
- "какая тренировка сегодня", "что сегодня по плану", "покажи сегодняшнюю тренировку" => show_today_workout.
- "дай план тренировок на неделю", "покажи план на неделю" => show_week_plan.
- "покажи следующую неделю тренировок", "что на следующей неделе" => show_next_week_plan.
- "покажи месячный план", "план на месяц" => show_month_plan.
- "план на следующий месяц" => show_next_month_plan.
- "что у меня в пятницу" => show_workout_on_date, date = ближайшая такая дата.

2. Замена сегодняшней тренировки:
- "сегодня вместо ног делаем плечи"
- "замени сегодняшнюю тренировку на плечи"
- "на сегодня поставь тренировку плеч"
- "сегодня тренировка: жим гантелей сидя, разводка, фронтальный подъем"
=> replace_today_workout.
Если упражнения перечислены, заполни workout.exercises.
Если только группа, заполни focus/focus_label.

3. Добавление новой тренировки:
- "добавь тренировку на сегодня/завтра/пятницу"
=> add_custom_workout.

4. Запись факта / тренировочная сессия:
- "записываем сегодняшнюю тренировку..."
- "жим гантелей сидя: 17.5 на 20, 20 на 12"
- "первый подход 17,5 кг на 20, второй 20 на 12"
=> log_workout_sets.
Если есть активная сессия и пользователь говорит "третий 20 на 10", "следующий 20 на 8" => continue_current_exercise.
Десятичную запятую превращай в точку: 17,5 => 17.5.

5. Исправления:
- "не 20, а 17.5"
- "во втором было 12, не 10"
- "на сегодня, не на пятницу"
=> correct_previous_action.
Если "удали последний подход" => delete_last_set.

6. Опасные удаления:
- "удали все тренировки"
- "очисти память"
- "удали все тренировки из памяти"
=> dangerous_delete.
Нельзя исполнять сразу.

7. Если смысл неясен => clarify или unknown.
Если команда поддерживаемая, confidence >= 0.75.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    return _safe_json_loads(response.choices[0].message.content or "{}")


async def _get_plan_for_period(telegram_user_id: str | None, start_date: str, end_date: str, title: str) -> str:
    items = await get_planned_workouts_in_period(
        telegram_user_id=telegram_user_id,
        start_date=start_date,
        end_date=end_date,
        include_cancelled=False,
    )
    return format_period_plan(items, title=title)


async def _create_or_replace_today_workout(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    today = date.today().isoformat()

    workout = parsed.get("workout") or {}
    exercises = _normalize_exercises(workout.get("exercises"))

    replacement = {
        "title": workout.get("title") or workout.get("focus_label") or "Кастомная тренировка",
        "focus": workout.get("focus"),
        "focus_label": workout.get("focus_label"),
        "notes": workout.get("notes"),
        "exercises": exercises,
    }

    # If user only gave focus, try to copy exercises from existing template.
    if not replacement["exercises"] and replacement.get("focus"):
        template = await get_latest_planned_workout_template_by_focus(
            telegram_user_id=telegram_user_id,
            focus=replacement.get("focus"),
            exclude_workout_id=None,
        )
        if template:
            copied = []
            for i, ex in enumerate(template.get("exercises") or [], start=1):
                copied.append({
                    "exercise_order": i,
                    "exercise_name": ex.get("exercise_name"),
                    "target_sets": ex.get("target_sets"),
                    "target_reps_min": ex.get("target_reps_min"),
                    "target_reps_max": ex.get("target_reps_max"),
                    "target_reps_text": ex.get("target_reps_text"),
                    "target_weight_kg": ex.get("target_weight_kg"),
                    "notes": ex.get("notes"),
                })
            replacement["exercises"] = copied
            replacement["notes"] = (replacement.get("notes") or "") + "\nУпражнения скопированы из шаблона."

    current = await get_today_planned_workout(telegram_user_id, today)

    if current:
        old = current["workout"]
        replacement_id = await replace_planned_workout(
            target_workout_id=old["id"],
            replacement=replacement,
            source_text=text,
        )
        updated = await get_today_planned_workout(telegram_user_id, today)
        return (
            "Заменил сегодняшнюю тренировку.\n\n"
            f"Было: {old.get('title') or old.get('focus_label')}\n\n"
            "Стало:\n"
            + format_planned_workout(updated)
        )

    # No workout today: create ad-hoc plan/workout for today.
    plan_id = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name="Ad hoc workout",
        period_type="day",
        start_date=today,
        end_date=today,
        source_text=text,
        notes="Created by Fitness Core v2",
        planned_workouts=[{
            "planned_date": today,
            "weekday": None,
            "sequence_number": 1,
            "is_floating": False,
            "title": replacement.get("title"),
            "focus": replacement.get("focus"),
            "focus_label": replacement.get("focus_label"),
            "workout_type": "custom",
            "status": "planned",
            "notes": replacement.get("notes"),
            "exercises": replacement.get("exercises") or [],
        }],
    )

    updated = await get_today_planned_workout(telegram_user_id, today)
    return (
        "На сегодня не было активной плановой тренировки. Создал новую.\n"
        f"ID плана: {plan_id}\n\n"
        + format_planned_workout(updated)
    )


async def _add_custom_workout(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    target_date = parsed.get("date") or date.today().isoformat()
    workout = parsed.get("workout") or {}
    exercises = _normalize_exercises(workout.get("exercises"))

    plan_id = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name="Ad hoc workout",
        period_type="day",
        start_date=target_date,
        end_date=target_date,
        source_text=text,
        notes="Created by Fitness Core v2",
        planned_workouts=[{
            "planned_date": target_date,
            "weekday": parsed.get("weekday"),
            "sequence_number": 1,
            "is_floating": False,
            "title": workout.get("title") or workout.get("focus_label") or "Кастомная тренировка",
            "focus": workout.get("focus"),
            "focus_label": workout.get("focus_label"),
            "workout_type": "custom",
            "status": "planned",
            "notes": workout.get("notes"),
            "exercises": exercises,
        }],
    )

    return (
        f"Добавил тренировку на {format_human_date(target_date)}.\n"
        f"ID плана: {plan_id}"
    )


async def _log_workout_sets(telegram_user_id: str | None, text: str, parsed: dict, active_session: dict | None) -> str:
    today = date.today().isoformat()
    workout_date = parsed.get("date") or today
    workout = parsed.get("workout") or {}
    logged_exercises = _normalize_logged_exercises(parsed.get("logged_exercises"))

    if not logged_exercises:
        return "Я понял, что ты записываешь тренировку, но не смог уверенно выделить подходы."

    workout_id = await save_fitness_workout_session_v2(
        telegram_user_id=telegram_user_id,
        workout_date=workout_date,
        workout_type="actual",
        focus=workout.get("focus"),
        focus_label=workout.get("focus_label"),
        source_text=text,
        notes=workout.get("notes"),
        exercises=logged_exercises,
    )

    current_exercise = logged_exercises[-1]["exercise_name"]
    total_sets = sum(len(ex["sets"]) for ex in logged_exercises)

    session_context = {
        "workout_id": workout_id,
        "workout_date": workout_date,
        "current_exercise": current_exercise,
        "session_status": "active",
        "started_at": _now_iso(),
        "last_activity_at": _now_iso(),
        "last_training_activity_at": _now_iso(),
        "last_action": "log_workout_sets",
    }

    await create_fitness_pending_decision(
        telegram_user_id=telegram_user_id,
        decision_type="active_workout_session",
        context=session_context,
        source_text=text,
    )

    lines = [
        "Записал тренировочную сессию.",
        f"ID тренировки: {workout_id}",
        "",
    ]

    for ex in logged_exercises:
        lines.append(f"{ex['exercise_name']}:")
        for s in ex["sets"]:
            weight = s.get("weight_kg")
            reps = s.get("reps")
            set_number = s.get("set_number")
            if weight is not None and reps is not None:
                lines.append(f"{set_number}) {weight} кг × {reps}")
            elif reps is not None:
                lines.append(f"{set_number}) {reps} повторений")
        lines.append("")

    lines.append(f"Всего записано подходов: {total_sets}")
    lines.append(f"Текущее упражнение: {current_exercise}")

    return "\n".join(lines).strip()


async def _continue_current_exercise(telegram_user_id: str | None, text: str, parsed: dict, active_session: dict | None) -> str:
    if not active_session or not active_session.get("workout_id"):
        return "Я понял подход, но не вижу активной тренировки. Скажи сначала: “Записываем сегодняшнюю тренировку...”"

    logged_exercises = _normalize_logged_exercises(parsed.get("logged_exercises"))

    # Sometimes parser may put only sets without exercise name.
    if not logged_exercises:
        sets = []
        for i, s in enumerate(parsed.get("sets") or [], start=1):
            if s.get("weight_kg") is not None or s.get("reps") is not None:
                sets.append({
                    "set_number": s.get("set_number") or i,
                    "weight_kg": s.get("weight_kg"),
                    "reps": s.get("reps"),
                    "rpe": s.get("rpe"),
                    "notes": s.get("notes"),
                })
        if sets:
            logged_exercises = [{
                "exercise_name": active_session.get("current_exercise"),
                "sets": sets,
            }]

    if not logged_exercises:
        return "Я понял, что это продолжение тренировки, но не смог выделить вес и повторы."

    exercise_name = logged_exercises[0].get("exercise_name") or active_session.get("current_exercise")
    sets = logged_exercises[0].get("sets") or []

    inserted = await append_fitness_workout_sets_v2(
        workout_id=int(active_session["workout_id"]),
        exercise_name=exercise_name,
        sets=sets,
        source_text=text,
    )

    active_session["current_exercise"] = exercise_name
    active_session["session_status"] = "active"
    active_session["last_activity_at"] = _now_iso()
    active_session["last_training_activity_at"] = _now_iso()
    active_session["last_action"] = "continue_current_exercise"

    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if pending and pending.get("decision_type") == "active_workout_session":
        await update_fitness_pending_decision_context(pending["id"], active_session)

    lines = [f"Записал подходы: {exercise_name}"]
    for s in sets:
        if s.get("weight_kg") is not None and s.get("reps") is not None:
            lines.append(f"- {s.get('weight_kg')} кг × {s.get('reps')}")
        elif s.get("reps") is not None:
            lines.append(f"- {s.get('reps')} повторений")
    lines.append(f"Добавлено подходов: {inserted}")

    return "\n".join(lines)


async def _delete_last_set(telegram_user_id: str | None, active_session: dict | None) -> str:
    if not active_session or not active_session.get("workout_id"):
        return "Не вижу активной тренировки, из которой можно удалить последний подход."

    deleted = await delete_last_fitness_set_v2(int(active_session["workout_id"]))
    if not deleted:
        return "Не нашёл последний подход для удаления."

    return (
        "Удалил последний подход:\n"
        f"{deleted.get('exercise_name')} — {deleted.get('weight_kg')} кг × {deleted.get('reps')}"
    )


async def _correct_previous_action(telegram_user_id: str | None, text: str, parsed: dict, active_session: dict | None) -> str:
    correction = parsed.get("correction") or {}
    field = correction.get("field")
    new_value = correction.get("new_value")

    if field in ("weight", "weight_kg", "reps") and active_session and active_session.get("workout_id"):
        updated = await update_last_fitness_set_v2(
            workout_id=int(active_session["workout_id"]),
            field="weight_kg" if field in ("weight", "weight_kg") else "reps",
            new_value=new_value,
        )
        if updated:
            return (
                "Исправил последний подход:\n"
                f"{updated.get('exercise_name')} — {updated.get('weight_kg')} кг × {updated.get('reps')}"
            )

    if field == "date":
        return (
            "Понял уточнение по дате. Сейчас я ещё не умею безопасно переносить уже созданное действие по такой короткой поправке. "
            "Скажи полной фразой, например: “поставь эту тренировку на сегодня”."
        )

    return "Понял, что это исправление, но не смог безопасно применить его автоматически."




def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_status(active_session: dict | None) -> str:
    if not active_session:
        return ""
    return active_session.get("session_status") or "active"


def _looks_like_set_message(text: str) -> bool:
    t = (text or "").lower().replace(",", ".")
    has_set_hint = any(x in t for x in [
        "перв", "втор", "трет", "четвер", "пят", "шест", "седьм",
        "следующ", "еще", "ещё", "последн"
    ])
    has_weight_reps = re.search(
        r"\d+(?:\.\d+)?\s*(?:кг|килограмм(?:ов|а)?|)?\s*(?:на|x|х|×)\s*\d+",
        t,
    )
    return bool(has_set_hint and has_weight_reps)


def _extract_set_number(text: str):
    t = (text or "").lower()
    mapping = [
        ("перв", 1),
        ("втор", 2),
        ("трет", 3),
        ("четвер", 4),
        ("пят", 5),
        ("шест", 6),
        ("седьм", 7),
        ("восьм", 8),
        ("девят", 9),
        ("десят", 10),
    ]
    for stem, value in mapping:
        if stem in t:
            return value
    return None




def _is_explicit_workout_recording_command(text: str) -> bool:
    """
    Long explicit workout-recording commands must go to the full AI parser,
    not to the short active-session regex parser.

    Example:
    “Записываем сегодняшнюю тренировку. Жим гантелей сидя.
     Первый подход 17.5 на 20, второй 20 на 12.”
    """
    t = (text or "").strip().lower().replace("ё", "е")

    explicit_start = any(x in t for x in [
        "записываем",
        "записываю",
        "записать тренировку",
        "запиши тренировку",
        "сегодняшнюю тренировку",
        "сегодняшняя тренировка",
        "начинаем тренировку",
        "начал тренировку",
    ])

    has_multiple_sets = (
        ("перв" in t and "втор" in t)
        or t.count(" на ") >= 2
        or t.count("×") >= 2
        or t.count(" x ") >= 2
        or t.count(" х ") >= 2
    )

    return explicit_start or (has_multiple_sets and len(t) > 45)

def _parse_set_from_short_text(text: str, active_session: dict | None) -> dict | None:
    if not active_session or not active_session.get("current_exercise"):
        return None

    t = (text or "").lower().replace(",", ".")
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:кг|килограмм(?:ов|а)?|)?\s*(?:на|x|х|×)\s*(\d+)",
        t,
    )
    if not m:
        return None

    return {
        "action": "continue_current_exercise",
        "confidence": 0.99,
        "logged_exercises": [
            {
                "exercise_name": active_session.get("current_exercise"),
                "sets": [
                    {
                        "set_number": _extract_set_number(text),
                        "weight_kg": float(m.group(1)),
                        "reps": int(m.group(2)),
                    }
                ],
            }
        ],
    }


def _fast_finish_response(text: str) -> str | None:
    t = (text or "").strip().lower().replace("ё", "е")

    danger_terms = [
        "удали",
        "сотри",
        "не сохраняй",
        "отмени тренировку",
        "удали тренировку",
        "очисти",
    ]

    continue_exact = {
        "нет",
        "не закончил",
        "не закончена",
        "продолжаю",
        "еще",
        "ещё",
        "еще делаю",
        "ещё делаю",
        "еще тренируюсь",
        "ещё тренируюсь",
        "продолжим",
        "пока нет",
        "не закрывай",
        "оставь открытой",
        "сейчас продолжу",
        "дай еще время",
        "дай ещё время",
    }

    continue_contains = [
        "не закончил",
        "не закрывай",
        "еще делаю",
        "ещё делаю",
        "еще тренируюсь",
        "ещё тренируюсь",
        "сейчас продолжу",
        "продолжаю",
        "дай еще",
        "дай ещё",
    ]

    finish_exact = {
        "да",
        "да закончил",
        "да, закончил",
        "закончил",
        "закончена",
        "все",
        "всё",
        "готово",
        "закрывай",
        "закрой",
        "сохрани",
        "сохраняй",
        "на сегодня все",
        "на сегодня всё",
        "тренировка закончена",
        "тренировку закончил",
        "хватит",
    }

    finish_contains = [
        "закончил тренировку",
        "тренировку закончил",
        "тренировка закончена",
        "закрывай тренировку",
        "закрой тренировку",
        "сохрани тренировку",
        "на сегодня все",
        "на сегодня всё",
        "на сегодня хватит",
    ]

    if any(x in t for x in danger_terms):
        return "danger"

    if t in continue_exact or any(x in t for x in continue_contains):
        return "continue"

    if t in finish_exact or any(x in t for x in finish_contains):
        return "finish"

    return None


def _parse_finish_confirmation_with_ai(text: str, active_session: dict | None) -> dict:
    system_prompt = f"""
Ты parser ответа на вопрос фитнес-ассистента: "Ты закончил тренировку?"

Активная сессия:
{json.dumps(active_session or {}, ensure_ascii=False)}

Пользователь ответил:
{text}

Верни строго JSON:
{{
  "action": "finish | continue | danger | unclear",
  "confidence": 0.0,
  "summary": ""
}}

Правила:
- finish: пользователь говорит, что закончил / хватит / закрывай / сохраняй.
- continue: пользователь говорит, что продолжает / ещё тренируется / сделает ещё подходы.
- danger: пользователь хочет удалить/не сохранять/стереть тренировку.
- unclear: непонятно.
Ответ только JSON.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    return _safe_json_loads(response.choices[0].message.content or "{}")


async def _close_active_workout_session(telegram_user_id: str | None, active_session: dict, reason: str = "finished") -> str:
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if pending and pending.get("decision_type") == "active_workout_session":
        await resolve_fitness_pending_decision(pending["id"], status="resolved")

    return "Ок, тренировку закрыл. Записанные подходы сохранил."


async def _continue_active_workout_session(telegram_user_id: str | None, active_session: dict) -> str:
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if pending and pending.get("decision_type") == "active_workout_session":
        active_session["session_status"] = "active"
        active_session["last_activity_at"] = _now_iso()
        await update_fitness_pending_decision_context(pending["id"], active_session)

    current = active_session.get("current_exercise") or "текущее упражнение"
    return f"Ок, продолжаем. Текущее упражнение: {current}."


async def try_handle_active_workout_message(telegram_user_id: str | None, text: str) -> str | None:
    """
    Called before generic router.

    Handles context-only active workout messages:
    - “Третий 20 на 10”
    - “закончил тренировку”
    - replies to “Ты закончил тренировку?”

    But it must NOT swallow explicit new commands like:
    - “Записываем сегодняшнюю тренировку. Первый подход..., второй...”
    - “Запиши план на следующую неделю...”
    """

    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if not pending or pending.get("decision_type") != "active_workout_session":
        return None

    active_session = pending.get("context_json") or {}
    status = _session_status(active_session)

    normalized = (text or "").strip().lower().replace("ё", "е")

    # Do not swallow explicit new planning commands.
    if "план" in normalized and any(w in normalized for w in ["запиши", "создай", "поставь", "составь"]):
        return None

    # Do not swallow full workout-recording commands.
    # They must go through the full Fitness Core v2 parser so multiple sets are preserved.
    if _is_explicit_workout_recording_command(text):
        return None

    # Finish/continue/danger commands should work even when session is just active,
    # not only when status == awaiting_finish_confirmation.
    fast = _fast_finish_response(text)

    if fast == "finish":
        return await _close_active_workout_session(telegram_user_id, active_session)

    if fast == "continue":
        return await _continue_active_workout_session(telegram_user_id, active_session)

    if fast == "danger":
        return (
            "Это опасное действие. Я не удаляю тренировку автоматически.\n"
            "Напиши явно: “да, удали текущую тренировку” или “нет, оставь”."
        )

    # If user sends a short set message, continue workout regardless of status.
    if _looks_like_set_message(text):
        parsed = _parse_set_from_short_text(text, active_session)
        if parsed:
            if status == "awaiting_finish_confirmation":
                active_session["session_status"] = "active"
                active_session["last_activity_at"] = _now_iso()
                await update_fitness_pending_decision_context(pending["id"], active_session)
            return await _continue_current_exercise(telegram_user_id, text, parsed, active_session)

    # If bot is waiting for finish confirmation, use AI parser as fallback.
    if status == "awaiting_finish_confirmation":
        parsed = _parse_finish_confirmation_with_ai(text, active_session)
        action = parsed.get("action")
        confidence = float(parsed.get("confidence") or 0)

        if confidence >= 0.65:
            if action == "finish":
                return await _close_active_workout_session(telegram_user_id, active_session)
            if action == "continue":
                return await _continue_active_workout_session(telegram_user_id, active_session)
            if action == "danger":
                return (
                    "Это опасное действие. Я не удаляю тренировку автоматически.\n"
                    "Напиши явно: “да, удали текущую тренировку” или “нет, оставь”."
                )

        return (
            "Я не понял, закончил ты тренировку или продолжаешь.\n"
            "Можешь ответить обычным текстом: “закончил” или “продолжаю”."
        )

    return None


async def handle_fitness_action_v2(telegram_user_id: str | None, text: str) -> str | None:
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    active_session = _active_session_context_from_pending(pending)

    history_reply = await handle_exercise_history_request(
        telegram_user_id=telegram_user_id,
        text=text,
        active_session=active_session,
    )
    if history_reply is not None:
        return history_reply

    parsed = parse_fitness_action_v2(text, active_session=active_session)

    action = parsed.get("action")
    confidence = float(parsed.get("confidence") or 0)

    if not action or action in ("unknown", "clarify") or confidence < 0.55:
        return None

    if action == "show_today_workout":
        data = await get_today_planned_workout(telegram_user_id, date.today().isoformat())
        if not data:
            return "На сегодня активная плановая тренировка не найдена."
        return "Сегодня по плану:\n\n" + format_planned_workout(data)

    if action == "show_week_plan":
        start, end = week_bounds()
        return await _get_plan_for_period(telegram_user_id, start, end, f"План недели ({start} — {end})")

    if action == "show_next_week_plan":
        start, end = next_week_bounds()
        return await _get_plan_for_period(telegram_user_id, start, end, f"План на следующую неделю ({start} — {end})")

    if action == "show_month_plan":
        start, end = month_bounds()
        return await _get_plan_for_period(telegram_user_id, start, end, f"План на текущий месяц ({start} — {end})")

    if action == "show_next_month_plan":
        start, end = next_month_bounds()
        return await _get_plan_for_period(telegram_user_id, start, end, f"План на следующий месяц ({start} — {end})")

    if action == "show_workout_on_date":
        target_date = parsed.get("date") or date.today().isoformat()
        data = await get_today_planned_workout(telegram_user_id, target_date)
        if not data:
            return f"На {format_human_date(target_date)} активная плановая тренировка не найдена."
        return f"Тренировка на {format_human_date(target_date)}:\n\n" + format_planned_workout(data)

    if action == "replace_today_workout":
        return await _create_or_replace_today_workout(telegram_user_id, text, parsed)

    if action == "add_custom_workout":
        return await _add_custom_workout(telegram_user_id, text, parsed)

    if action == "log_workout_sets":
        return await _log_workout_sets(telegram_user_id, text, parsed, active_session)

    if action == "continue_current_exercise":
        return await _continue_current_exercise(telegram_user_id, text, parsed, active_session)

    if action == "delete_last_set":
        return await _delete_last_set(telegram_user_id, active_session)

    if action == "correct_previous_action":
        return await _correct_previous_action(telegram_user_id, text, parsed, active_session)

    if action == "dangerous_delete":
        return (
            "Это опасное действие, я не буду удалять историю тренировок автоматически.\n\n"
            "Я могу безопасно сделать одно из двух:\n"
            "- очистить только план текущей недели: /fitness_reset_week\n"
            "- отменить конкретную плановую тренировку\n\n"
            "Фактическую историю тренировок без отдельного жёсткого подтверждения не трогаю."
        )

    return None
