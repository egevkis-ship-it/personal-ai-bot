from __future__ import annotations
import json
import re
from datetime import date, datetime, timezone

from app.ai import claude_client
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
from app.modules.fitness.router_hardening import handle_router_hardening
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

        # Merge warmup_sets into notes for storage
        notes = ex.get("notes") or ""
        warmup = ex.get("warmup_sets") or []
        if warmup:
            warmup_str = "Разминка: " + ", ".join(
                f"{w.get('weight_kg')}×{w.get('reps_min', '')}-{w.get('reps_max', '')}"
                if w.get('reps_max') else f"{w.get('weight_kg')}×{w.get('reps_min', '')}"
                for w in warmup
            )
            notes = (notes + "\n" + warmup_str).strip() if notes else warmup_str

        result.append({
            "exercise_order": ex.get("exercise_order") or i,
            "exercise_name": name,
            "target_sets": sets,
            "target_reps_min": reps_min,
            "target_reps_max": reps_max,
            "target_reps_text": reps_text,
            "target_weight_kg": weight,
            "notes": notes or None,
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


_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "среду", "четверг", "пятница", "пятницу", "суббота", "субботу", "воскресенье"]


def _looks_like_weekly_plan(text: str) -> bool:
    """Detect multi-day plan: several day-name headers with exercise lists."""
    t = text.lower()
    days_found = sum(1 for d in _WEEKDAYS_RU if d in t)
    has_exercises = bool(re.search(r"^\s*\d+\.", text, re.MULTILINE))
    return days_found >= 2 and has_exercises


def _looks_like_complex_plan(text: str) -> bool:
    """Detect structured multi-exercise workout plan text."""
    t = text.lower()
    has_numbered = bool(re.search(r"^\s*\d+\.", text, re.MULTILINE))
    has_sets_pattern = bool(re.search(r"\d+\s*[×x×]\s*\d+", text))
    has_kg = "кг" in t
    has_warmup = any(x in t for x in ["разминка", "рабочие подходы", "разминочн"])
    has_many_exercises = len(re.findall(r"^\s*\d+\.", text, re.MULTILINE)) >= 3
    return (has_numbered and has_sets_pattern and has_kg) or (has_warmup and has_sets_pattern) or has_many_exercises


async def parse_complex_workout_plan(text: str, target_date: str | None = None) -> dict:
    """
    Parse a rich multi-exercise workout plan with warm-up sets, working sets,
    equipment notes, and rep ranges. Returns dict compatible with add_custom_workout.
    """
    today = target_date or date.today().isoformat()

    system_prompt = f"""Ты парсер структурированной программы тренировки. Сегодня: {today}.

Пользователь описал детальную тренировку. Распарси её ПОЛНОСТЬЮ в JSON.

ПРАВИЛА ПАРСИНГА:
- Разминочные подходы (секция "Разминка:") → sets с пометкой "is_warmup": true
- Рабочие подходы (секция "Рабочие подходы:" или без метки) → is_warmup: false
- Диапазон повторений "10–12" или "10-12" → target_reps_min=10, target_reps_max=12
- "90 кг × 10–12 × 4 подхода" → target_sets=4, weight=90, reps_min=10, reps_max=12
- "25 кг × 10–12 × 4 подхода" → target_sets=4, weight=25, reps_min=10, reps_max=12
- Настройки тренажёра, пометки ("не до отказа", "сидушка — 2 дырки") → notes упражнения
- Запятая или тире в весе: "8–9 кг" → weight_min=8, weight_max=9, target_weight_kg=8.5
- Если дата явно указана ("завтра", "понедельник", конкретное число) — вычисли planned_date

Верни JSON строго:
{{
  "action": "add_custom_workout",
  "confidence": 0.95,
  "date": "{today}",
  "workout": {{
    "title": "Грудь силовая + дельта + трицепс",
    "focus": "chest",
    "focus_label": "грудь",
    "notes": null,
    "exercises": [
      {{
        "exercise_name": "Жим штанги лёжа",
        "target_sets": 4,
        "target_reps_min": 10,
        "target_reps_max": 12,
        "target_weight_kg": 90,
        "notes": "Разминка: 20×8, 50×8, 70×6, 80×3",
        "warmup_sets": [
          {{"weight_kg": 20, "reps_min": 8, "reps_max": 10}},
          {{"weight_kg": 50, "reps_min": 8, "reps_max": 10}},
          {{"weight_kg": 70, "reps_min": 6, "reps_max": 8}},
          {{"weight_kg": 80, "reps_min": 3, "reps_max": 5}}
        ]
      }},
      {{
        "exercise_name": "Жим гантелей под углом",
        "target_sets": 4,
        "target_reps_min": 10,
        "target_reps_max": 12,
        "target_weight_kg": 25,
        "notes": null,
        "warmup_sets": []
      }}
    ]
  }},
  "summary": "Тренировка грудь + дельта + трицепс на {today}"
}}

Только JSON. Без markdown. Распарси ВСЕ упражнения из текста."""

    response = await claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )
    return _safe_json_loads(response.content[0].text if response.content else "{}")


async def parse_weekly_plan(text: str) -> dict:
    """Parse a multi-day weekly/monthly plan into multiple planned_workouts."""
    today = date.today().isoformat()
    week_start, week_end = week_bounds()
    next_start, next_end = next_week_bounds()

    system_prompt = f"""Ты парсер недельной/месячной программы тренировок. Сегодня: {today}.
Текущая неделя: {week_start} — {week_end}. Следующая: {next_start} — {next_end}.

Пользователь прислал расписание на несколько дней. Распарси КАЖДЫЙ день как отдельную тренировку.

ПРАВИЛА:
- Каждый заголовок дня (Понедельник, Вторник и т.д.) → отдельный объект в planned_workouts
- Определи planned_date: если "на эту неделю" — ближайший такой день на текущей неделе
- "90×8–12×4" → target_sets=4, weight=90, reps_min=8, reps_max=12
- "65/70/70/65×10–12" → weight = первый вес (65), notes="прогрессия: 65/70/70/65"
- "только если колени спокойны", "если плечи живые" → notes упражнения
- Определи focus каждого дня: chest/back/legs/shoulders/arms/full_body
- Тире в диапазоне весов "8–9 кг" → target_weight_kg = среднее (8.5)

Верни JSON:
{{
  "action": "create_weekly_plan",
  "confidence": 0.95,
  "plan": {{
    "plan_name": "Программа на неделю",
    "period_type": "week",
    "start_date": "{week_start}",
    "end_date": "{week_end}",
    "planned_workouts": [
      {{
        "planned_date": "YYYY-MM-DD",
        "weekday": "monday",
        "title": "Грудь + трицепс",
        "focus": "chest",
        "focus_label": "грудь + трицепс",
        "notes": null,
        "exercises": [
          {{
            "exercise_name": "Жим штанги лёжа",
            "target_sets": 4,
            "target_reps_min": 8,
            "target_reps_max": 12,
            "target_weight_kg": 90,
            "notes": null
          }}
        ]
      }}
    ]
  }},
  "summary": "Недельная программа: 5 тренировок"
}}

Только JSON. Без markdown. Распарси ВСЕ дни и ВСЕ упражнения полностью."""

    response = await claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )
    return _safe_json_loads(response.content[0].text if response.content else "{}")


async def _save_weekly_plan(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    plan_data = parsed.get("plan") or {}
    planned_workouts_raw = plan_data.get("planned_workouts") or []

    if not planned_workouts_raw:
        return "Не смог распарсить расписание. Убедись что каждый день начинается с названия (Понедельник, Вторник и т.д.)."

    planned_workouts = _normalize_exercises_plan(planned_workouts_raw)

    plan_id = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name=plan_data.get("plan_name") or "Недельная программа",
        period_type=plan_data.get("period_type") or "week",
        start_date=plan_data.get("start_date"),
        end_date=plan_data.get("end_date"),
        source_text=text,
        notes=plan_data.get("notes"),
        planned_workouts=planned_workouts,
    )

    count = len(planned_workouts)
    days_summary = []
    for pw in planned_workouts[:5]:
        date_str = pw.get("planned_date", "")[:10] if pw.get("planned_date") else pw.get("weekday", "—")
        title = pw.get("title") or pw.get("focus_label") or "Тренировка"
        ex_count = len(pw.get("exercises") or [])
        days_summary.append(f"  • {date_str}: {title} ({ex_count} упр.)")

    lines = [
        f"✅ Записал программу на {count} тренировок. ID: {plan_id}",
        "",
    ] + days_summary

    if count > 5:
        lines.append(f"  ... и ещё {count - 5}")

    return "\n".join(lines)


async def parse_fitness_action_v2(text: str, active_session: dict | None = None) -> dict:
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
  "action": "show_today_workout | show_week_plan | show_next_week_plan | show_month_plan | show_next_month_plan | show_workout_on_date | replace_today_workout | add_custom_workout | log_workout_sets | continue_current_exercise | correct_previous_action | delete_last_set | edit_plan | show_progress | add_note | import_program | export_workouts | dangerous_delete | unknown | clarify",
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
    "set_number": null,
    "note_text": null
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

7. Редактирование плана:
- "добавь жим в сегодняшнюю тренировку"
- "убери тягу из плана на пятницу"
- "замени жим лёжа на жим гантелей в тренировке груди"
- "поменяй тренировки местами — перенеси пятницу на среду"
- "измени веса в плане"
- "давай её изменим", "измени план"
=> edit_plan. Поставь в target.exercise_name что менять.

8. Прогресс и история:
- "покажи мои результаты", "какой у меня прогресс по жиму", "история тренировок"
- "мои рекорды", "последние тренировки", "статистика"
=> show_progress.

9. Заметки и комментарии:
- "добавь заметку к тренировке: не забыть лямки"
- "запиши комментарий: хорошо прошло, увеличить вес"
- "заметка к жиму: локти ближе к телу"
- "комментарий к плану на пятницу: принести резинки"
=> add_note. Положи текст заметки в target.note_text, имя упражнения (если есть) в target.exercise_name.

10. Импорт программы тренировок:
- "вот моя программа: понедельник — грудь: жим 4x8..."
- "загрузи программу тренировок" (если текст содержит структурированный план)
- "запиши программу на 4 недели"
=> import_program. Данные плана кладёшь в поле plan (как для create_plan).

11. Экспорт тренировок:
- "выгрузи мои тренировки", "экспортируй историю", "дай мне данные тренировок"
=> export_workouts.

12. Если смысл неясен => clarify или unknown.
Если команда поддерживаемая, confidence >= 0.75.
"""

    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )

    return _safe_json_loads(response.content[0].text if response.content else "{}")


async def _parse_finish_confirmation_with_ai_async(text: str, active_session: dict | None) -> dict:
    system_prompt = (
        f"Ты parser ответа на вопрос: 'Ты закончил тренировку?'\n"
        f"Активная сессия: {json.dumps(active_session or {}, ensure_ascii=False)}\n"
        "Верни строго JSON: {\"action\": \"finish|continue|danger|unclear\", \"confidence\": 0.0, \"summary\": \"\"}\n"
        "finish — закончил/хватит/сохраняй. continue — продолжает. danger — удалить. unclear — непонятно."
    )
    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=128,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )
    return _safe_json_loads(response.content[0].text if response.content else "{}")


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

    ex_count = len(exercises)
    ex_names = ", ".join(e["exercise_name"] for e in exercises[:4])
    if ex_count > 4:
        ex_names += f" и ещё {ex_count - 4}"

    lines = [
        f"Записал тренировку на {format_human_date(target_date)}.",
        f"ID плана: {plan_id}",
        f"Упражнений: {ex_count} — {ex_names}",
    ]
    if workout.get("notes"):
        lines.append(f"📝 {workout.get('notes')}")

    return "\n".join(lines)


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
    """Kept for sync compatibility — prefer the async version."""
    return {"action": "unclear", "confidence": 0.0, "summary": ""}


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
        parsed = await _parse_finish_confirmation_with_ai_async(text, active_session)
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


async def _edit_plan(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    from app.db import (
        get_best_planned_workout_for_edit,
        add_exercise_to_planned_workout,
        remove_exercise_from_planned_workout,
        replace_exercise_in_planned_workout,
    )
    target = parsed.get("target") or {}
    exercise_name = target.get("exercise_name")
    target_date = parsed.get("date")

    workout = await get_best_planned_workout_for_edit(telegram_user_id, target_date=target_date)
    if not workout:
        return "Не нашёл активный план для редактирования. Сначала создай план."

    workout_id = workout.get("id")
    summary = parsed.get("summary") or ""

    # Ask Claude to figure out exact edit intent
    edit_prompt = f"""
Пользователь хочет изменить план тренировки (ID {workout_id}).

Запрос: {text}
Текущий план: {json.dumps(workout, ensure_ascii=False, default=str)}

Верни JSON:
{{
  "operation": "add | remove | replace | unknown",
  "exercise_name": "название упражнения",
  "new_exercise_name": "новое название (только для replace)",
  "sets": null,
  "reps_min": null,
  "reps_max": null,
  "weight_kg": null
}}
Только JSON.
"""
    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=256,
        system="Ты parser команд редактирования фитнес-плана. Ответ только JSON.",
        messages=[{"role": "user", "content": edit_prompt}],
    )
    edit_parsed = _safe_json_loads(response.content[0].text if response.content else "{}")

    operation = edit_parsed.get("operation")
    ex_name = edit_parsed.get("exercise_name") or exercise_name

    if operation == "add" and ex_name:
        result = await add_exercise_to_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=workout_id,
            exercise_name=ex_name,
            target_sets=edit_parsed.get("sets") or 3,
            target_reps_min=edit_parsed.get("reps_min"),
            target_reps_max=edit_parsed.get("reps_max"),
            target_weight_kg=edit_parsed.get("weight_kg"),
            source_text=text,
        )
        if result.get("ok"):
            return f"Добавил {ex_name} в план."
        return result.get("message") or f"Не удалось добавить {ex_name}."

    if operation == "remove" and ex_name:
        result = await remove_exercise_from_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=workout_id,
            exercise_name=ex_name,
            source_text=text,
        )
        if result.get("ok"):
            return f"Убрал {ex_name} из плана."
        return result.get("message") or f"Упражнение {ex_name!r} не найдено в плане."

    if operation == "replace" and ex_name:
        new_name = edit_parsed.get("new_exercise_name")
        if not new_name:
            return "Не понял, на что заменить. Уточни: 'замени X на Y'."
        result = await replace_exercise_in_planned_workout(
            telegram_user_id=telegram_user_id,
            target_date=target_date or date.today().isoformat(),
            old_exercise_name=ex_name,
            new_exercise_name=new_name,
            source_text=text,
        )
        if result.get("ok"):
            return f"Заменил {ex_name} на {new_name}."
        return result.get("message") or f"Упражнение {ex_name!r} не найдено."

    return f"Уточни что изменить: добавить, убрать или заменить упражнение?\n\nТекущий план: {workout.get('title', 'без названия')}"


async def _show_progress(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    from app.db import get_last_workout, get_last_measurement, get_recent_exercise_history

    target = parsed.get("target") or {}
    exercise_name = target.get("exercise_name")

    lines = []

    if exercise_name:
        history = await get_recent_exercise_history(telegram_user_id, exercise_key=exercise_name, limit_workouts=5)
        if not history:
            return f"Нет истории по упражнению «{exercise_name}»."
        lines.append(f"История: {exercise_name}")
        for entry in history[:5]:
            date_str = str(entry.get("workout_date", ""))[:10]
            sets_info = ", ".join(
                f"{s.get('weight_kg')} кг × {s.get('reps')}"
                for s in (entry.get("sets") or [])
            )
            lines.append(f"  {date_str}: {sets_info}")
    else:
        last = await get_last_workout(telegram_user_id)
        if last:
            lines.append(f"Последняя тренировка: {str(last.get('workout_date', ''))[:10]}")
            for ex in (last.get("exercises") or [])[:5]:
                sets_info = ", ".join(
                    f"{s.get('weight_kg')} кг × {s.get('reps')}"
                    for s in (ex.get("sets") or [])
                )
                lines.append(f"  {ex.get('exercise_name')}: {sets_info}")

        measurement = await get_last_measurement(telegram_user_id)
        if measurement:
            lines.append(
                f"\nПоследние замеры ({str(measurement.get('measured_at', ''))[:10]}): "
                f"{measurement.get('weight_kg')} кг"
            )

    if not lines:
        return "Данных пока нет. Начни записывать тренировки!"

    return "\n".join(lines)


async def _add_note(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    from app.db import get_best_planned_workout_for_edit, get_last_workout
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    target = parsed.get("target") or {}
    note_text = target.get("note_text") or text.strip()
    exercise_name = target.get("exercise_name")
    target_date = parsed.get("date")

    # Try to add to planned workout first
    planned = await get_best_planned_workout_for_edit(telegram_user_id, target_date=target_date)
    if planned and planned.get("ok") if isinstance(planned, dict) else planned:
        workout_id = planned.get("id") or planned.get("planned_workout_id")
        if workout_id:
            async with get_session() as session:
                if exercise_name:
                    # Note on specific exercise in plan
                    await session.execute(sql_text("""
                        UPDATE planned_exercises
                        SET notes = :notes
                        WHERE planned_workout_id = :workout_id
                          AND lower(exercise_name) LIKE lower(:exercise_name)
                    """), {"notes": note_text, "workout_id": workout_id,
                           "exercise_name": f"%{exercise_name}%"})
                    return f"Добавил заметку к упражнению «{exercise_name}»:\n📝 {note_text}"
                else:
                    # Note on whole planned workout
                    await session.execute(sql_text("""
                        UPDATE planned_workouts SET notes = :notes WHERE id = :workout_id
                    """), {"notes": note_text, "workout_id": workout_id})
                    return f"Добавил заметку к тренировке:\n📝 {note_text}"

    # Fall back to last recorded workout
    last = await get_last_workout(telegram_user_id)
    if last:
        async with get_session() as session:
            await session.execute(sql_text("""
                UPDATE fitness_workouts SET notes = :notes WHERE id = :workout_id
            """), {"notes": note_text, "workout_id": last["id"]})
            return f"Добавил заметку к последней тренировке ({str(last.get('workout_date', ''))[:10]}):\n📝 {note_text}"

    return "Не нашёл тренировку для заметки. Укажи конкретную дату."


async def _import_program(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    from app.db import save_training_plan
    from app.modules.fitness.formatter import format_period_plan

    plan_data = parsed.get("plan") or {}
    planned_workouts = plan_data.get("planned_workouts") or []

    if not planned_workouts:
        return (
            "Не смог разобрать программу тренировок из текста.\n\n"
            "Отправь в формате:\n"
            "Понедельник — грудь: жим 4×8, разводка 3×12\n"
            "Среда — спина: тяга 4×8, подтягивания 3×10\n"
            "Пятница — ноги: приседания 4×8, жим ногами 3×12"
        )

    workouts_normalized = _normalize_exercises_plan(planned_workouts)
    result = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name=plan_data.get("plan_name") or "Импортированная программа",
        period_type=plan_data.get("period_type") or "custom",
        start_date=plan_data.get("start_date"),
        end_date=plan_data.get("end_date"),
        source_text=text,
        notes=plan_data.get("notes"),
        planned_workouts=workouts_normalized,
    )

    count = result.get("created_count", len(planned_workouts))
    return f"Программа импортирована. Создано {count} тренировок."


def _normalize_exercises_plan(planned_workouts: list[dict]) -> list[dict]:
    result = []
    for pw in planned_workouts:
        exercises = _normalize_exercises(pw.get("exercises"))
        result.append({**pw, "exercises": exercises})
    return result


async def _export_workouts(telegram_user_id: str | None) -> str:
    from app.db import get_last_workout
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    async with get_session() as session:
        result = await session.execute(sql_text("""
            SELECT
                w.workout_date,
                w.focus_label,
                w.notes AS workout_notes,
                s.exercise_name,
                s.set_number,
                s.weight_kg,
                s.reps,
                s.rpe,
                s.notes AS set_notes
            FROM fitness_workouts w
            JOIN fitness_exercise_sets s ON s.workout_id = w.id
            WHERE w.telegram_user_id = :uid
            ORDER BY w.workout_date DESC, w.id DESC, s.set_number ASC
            LIMIT 500
        """), {"uid": telegram_user_id})
        rows = result.fetchall()

    if not rows:
        return "Нет записанных тренировок для экспорта."

    lines = ["Дата | Фокус | Упражнение | Подход | Вес | Повторы | RPE | Заметка"]
    lines.append("—" * 60)
    for r in rows:
        weight = f"{r.weight_kg} кг" if r.weight_kg is not None else "—"
        rpe = str(r.rpe) if r.rpe else "—"
        notes = r.set_notes or ""
        lines.append(
            f"{str(r.workout_date)[:10]} | {r.focus_label or '—'} | {r.exercise_name} | "
            f"{r.set_number} | {weight} | {r.reps} | {rpe} | {notes}"
        )

    # Telegram message limit ~4096 chars
    output = "\n".join(lines)
    if len(output) > 3800:
        output = output[:3800] + f"\n\n... (показаны первые {len(rows)} записей)"

    return f"```\n{output}\n```"


async def _ask_clarification(text: str) -> str:
    """Generate a helpful clarification question instead of going silent."""
    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=256,
        system=(
            "Ты фитнес-ассистент. Пользователь написал что-то, но ты не уверен что именно нужно сделать. "
            "Напиши ОДНО короткое уточняющее сообщение (1-3 предложения) на русском. "
            "Скажи что ты понял и что именно непонятно. Предложи конкретные варианты что сделать. "
            "Не используй markdown. Будь конкретным и дружелюбным."
        ),
        messages=[{"role": "user", "content": f"Пользователь написал: {text}"}],
    )
    return response.content[0].text if response.content else "Не совсем понял — уточни что сделать?"


async def handle_fitness_action_v2(telegram_user_id: str | None, text: str) -> str | None:
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    active_session = _active_session_context_from_pending(pending)

    hardening_reply = await handle_router_hardening(telegram_user_id, text)
    if hardening_reply is not None:
        return hardening_reply

    history_reply = await handle_exercise_history_request(
        telegram_user_id=telegram_user_id,
        text=text,
        active_session=active_session,
    )
    if history_reply is not None:
        return history_reply

    # Detect multi-day weekly/monthly plan BEFORE main parser
    if _looks_like_weekly_plan(text) and not active_session:
        weekly_parsed = await parse_weekly_plan(text)
        if weekly_parsed.get("action") == "create_weekly_plan" and \
                weekly_parsed.get("plan", {}).get("planned_workouts"):
            return await _save_weekly_plan(telegram_user_id, text, weekly_parsed)

    # Detect structured single-day complex plan
    if _looks_like_complex_plan(text) and not active_session:
        complex_parsed = await parse_complex_workout_plan(text)
        if complex_parsed.get("action") in ("add_custom_workout", "replace_today_workout") and \
                complex_parsed.get("workout", {}).get("exercises"):
            return await _add_custom_workout(telegram_user_id, text, complex_parsed)

    parsed = await parse_fitness_action_v2(text, active_session=active_session)

    action = parsed.get("action")
    confidence = float(parsed.get("confidence") or 0)

    if not action or action in ("unknown", "clarify") or confidence < 0.55:
        return await _ask_clarification(text)

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

    if action == "edit_plan":
        return await _edit_plan(telegram_user_id, text, parsed)

    if action == "show_progress":
        return await _show_progress(telegram_user_id, text, parsed)

    if action == "add_note":
        return await _add_note(telegram_user_id, text, parsed)

    if action == "import_program":
        return await _import_program(telegram_user_id, text, parsed)

    if action == "export_workouts":
        return await _export_workouts(telegram_user_id)

    if action == "dangerous_delete":
        return (
            "Это опасное действие, я не буду удалять историю тренировок автоматически.\n\n"
            "Я могу безопасно сделать одно из двух:\n"
            "- очистить только план текущей недели: /fitness_reset_week\n"
            "- отменить конкретную плановую тренировку\n\n"
            "Фактическую историю тренировок без отдельного жёсткого подтверждения не трогаю."
        )

    return None
