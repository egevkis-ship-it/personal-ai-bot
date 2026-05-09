from __future__ import annotations

import json
from datetime import date, timedelta


RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def today_iso() -> str:
    return date.today().isoformat()


def tomorrow_iso() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def normalize_date_value(value: str | None) -> str | None:
    if not value:
        return None

    t = value.strip().lower().replace("ё", "е")

    if t in {"today", "сегодня"}:
        return today_iso()

    if t in {"tomorrow", "завтра"}:
        return tomorrow_iso()

    # already ISO
    if len(t) == 10 and t[4] == "-" and t[7] == "-":
        return t

    parts = t.split()
    if len(parts) >= 2:
        try:
            day = int(parts[0])
            month = RU_MONTHS.get(parts[1])
            if month:
                return date(date.today().year, month, day).isoformat()
        except Exception:
            return None

    return None


def normalize_planning_action(parsed: dict) -> dict:
    parsed = parsed or {}
    action = parsed.get("action") or "unknown"

    parsed["action"] = action
    parsed["confidence"] = float(parsed.get("confidence") or 0)

    for key in ["target_date", "source_date", "start_date", "end_date"]:
        if parsed.get(key):
            parsed[key] = normalize_date_value(str(parsed.get(key)))

    # normalize scope
    if action == "cancel_planned_workouts":
        parsed["affects"] = "planned_only"
        parsed["requires_confirmation"] = True

    if action == "replace_exercise":
        parsed["preserve_parameters"] = True

    return parsed


def fast_parse_planning_action(text: str) -> dict | None:
    """
    Deterministic shortcut layer.
    It is intentionally small. If user says something slightly different,
    AI parser should handle it.
    """
    t = (text or "").strip().lower().replace("ё", "е")

    if not t:
        return None

    # Exact-ish cleanup request.
    if "пуст" in t and "трениров" in t and any(x in t for x in ["удали", "очист", "убери"]):
        return {
            "action": "cleanup_empty_planned",
            "confidence": 0.98,
            "summary": "Очистить пустые плановые тренировки",
        }

    # Show next workout.
    if "следующ" in t and "трениров" in t and "недел" not in t:
        return {
            "action": "show_next_workout",
            "confidence": 0.95,
            "include_weights": "вес" in t,
            "summary": "Показать следующую плановую тренировку",
        }

    # Explicit all planned cancellation.
    if "планов" in t and "трениров" in t and "все" in t and any(x in t for x in ["удали", "отмени", "очист", "снес"]):
        return {
            "action": "cancel_planned_workouts",
            "confidence": 0.96,
            "scope": "all",
            "affects": "planned_only",
            "requires_confirmation": True,
            "summary": "Отменить все активные плановые тренировки",
        }

    return None


async def parse_planned_workout_action(text: str, context: dict | None = None) -> dict:
    """
    Parser-first layer for planned workout operations.

    Returns structured action JSON:
    - show_workout_on_date
    - show_next_workout
    - show_period_plan
    - create_custom_workout
    - replace_exercise
    - move_workout
    - copy_workout
    - cancel_planned_workouts
    - cleanup_empty_planned
    - unknown
    """
    fast = fast_parse_planning_action(text)
    if fast:
        return normalize_planning_action(fast)

    from app.ai import client

    context = context or {}

    system_prompt = f"""
Ты parser плановых тренировок фитнес-бота.

Главный принцип:
Пользователь говорит свободным языком. Нельзя требовать точных команд.
Твоя задача — вернуть structured action JSON.

Сегодня: {today_iso()}
Завтра: {tomorrow_iso()}

Контекст:
{json.dumps(context, ensure_ascii=False)}

Верни строго JSON без markdown:

{{
  "action": "show_workout_on_date | show_next_workout | show_period_plan | create_custom_workout | replace_exercise | move_workout | copy_workout | cancel_planned_workouts | cleanup_empty_planned | unknown",
  "confidence": 0.0,
  "target_date": null,
  "source_date": null,
  "start_date": null,
  "end_date": null,
  "scope": null,
  "include_weights": false,
  "old_exercise_name": null,
  "new_exercise_name": null,
  "has_workout_details": false,
  "planned_only": true,
  "requires_confirmation": false,
  "summary": ""
}}

Смысл действий:

1. show_workout_on_date:
- "покажи тренировку на сегодня"
- "что у меня сегодня"
- "какие упражнения сегодня"
- "что у меня на 11 мая"
- "дай тренировку за 11 мая с весами"
target_date обязательно.
include_weights=true если пользователь просит веса.

2. show_next_workout:
- "какая следующая тренировка"
- "дай следующую тренировку"
- "следующая тренировка с весами"
include_weights=true если есть "с весами", "веса", "какие веса".

3. show_period_plan:
- "покажи неделю"
- "покажи следующую неделю"
- "план на месяц"
scope: current_week | next_week | month.

4. create_custom_workout:
- "давай добавим тренировку сегодня"
- "хочу сегодня потренироваться"
- "создай тренировку на завтра"
- "добавь тренировку сегодня: жим лежа 4 по 10..."
target_date обязательно.
has_workout_details=true если в сообщении уже есть упражнения/подходы/веса.
has_workout_details=false если пользователь только просит создать тренировку без деталей.

5. replace_exercise:
- "замени бицепс стоя на бицепс сидя"
- "поменяй жим гантелей на жим штанги"
- "вместо бицепса стоя поставь бицепс сидя"
old_exercise_name и new_exercise_name обязательны.
target_date по умолчанию сегодня.
preserve_parameters=true подразумевается.

6. move_workout:
- "перенеси тренировку с 11 мая на сегодня"
source_date и target_date обязательны.

7. copy_workout:
- "хочу сделать тренировку 11 мая сегодня"
- "возьми тренировку с 11 мая на сегодня"
source_date и target_date обязательны.

8. cancel_planned_workouts:
- "удали тренировки на следующей неделе"
- "отмени план следующей недели"
- "удали все плановые тренировки"
- "снеси весь план тренировок"
- "начать план заново"
Это касается только плановых тренировок, не фактической истории.
requires_confirmation=true.
scope:
  all = все активные плановые тренировки
  current_week = текущая неделя
  next_week = следующая неделя
  future = от сегодня и дальше
  period = если есть start_date/end_date

9. cleanup_empty_planned:
- "удали пустые тренировки"
- "очисти пустые плановые тренировки"

10. unknown:
если это не про плановые тренировки.

Критично:
- "удали всю историю тренировок" НЕ является cancel_planned_workouts. Это unknown или dangerous_history_delete.
- "замени X на Y" — это replace_exercise, НЕ замена всей тренировки.
- Если пользователь говорит "плановые тренировки", это planned_only=true.
- Ответ только JSON.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    parsed = safe_json_loads(response.choices[0].message.content or "{}")
    return normalize_planning_action(parsed)
