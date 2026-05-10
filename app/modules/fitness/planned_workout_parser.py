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



WORKOUT_TERMS = [
    "тренировка",
    "тренировки",
    "треня",
    "треню",
    "трени",
    "тренька",
    "треньку",
    "треньк",
    "треньки",
    "треньке",
    "тренечку",
    "занятие",
    "занятия",
    "воркаут",
    "workout",
    "зал",
    "план тренировок",
    "тренировочный план",
    "программа тренировок",
    "прога",
]

PLAN_TERMS = [
    "план",
    "планов",
    "заплан",
    "программа",
    "прога",
    "план тренировок",
    "тренировочный план",
]

DELETE_TERMS = [
    "удали",
    "удалить",
    "убери",
    "убрать",
    "отмени",
    "отменить",
    "снеси",
    "снести",
    "очисти",
    "почисти",
    "обнули",
    "сбрось",
    "сними",
    "снять",
    "начать заново",
    "начнем заново",
    "начнём заново",
    "перезапусти",
    "пересобери",
]

CREATE_TERMS = [
    "добавь",
    "добавить",
    "создай",
    "создать",
    "запланируй",
    "поставь",
    "запиши",
    "накидай",
    "собери",
    "составь",
    "сделай",
    "хочу сделать",
    "хочу потренироваться",
    "давай добавим",
]

SHOW_TERMS = [
    "покажи",
    "дай",
    "выведи",
    "что у меня",
    "какая",
    "какие упражнения",
    "что сегодня",
    "что по плану",
    "что тренируем",
    "следующая",
    "следующий",
]

REPLACE_TERMS = [
    "замени",
    "поменяй",
    "смени",
    "вместо",
    "поставь вместо",
    "лучше сделаем",
]


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _has_workout_object(text: str) -> bool:
    return _contains_any(text, WORKOUT_TERMS) or _contains_any(text, PLAN_TERMS)



def _next_weekday_from_text(text: str, weekday_index: int) -> str:
    today = date.today()
    delta = weekday_index - today.weekday()
    if delta < 0:
        delta += 7
    return (today + timedelta(days=delta)).isoformat()


def _extract_ru_dates_from_text(text: str) -> list[str]:
    t = (text or "").lower().replace("ё", "е")
    dates: list[str] = []

    import re

    matches = re.findall(
        r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b",
        t,
    )
    for day_s, month_s in matches:
        month = RU_MONTHS.get(month_s)
        if month:
            dates.append(date(date.today().year, month, int(day_s)).isoformat())

    weekday_map = {
        "понедельник": 0,
        "понедельника": 0,
        "вторник": 1,
        "вторника": 1,
        "среда": 2,
        "среду": 2,
        "среды": 2,
        "четверг": 3,
        "четверга": 3,
        "пятница": 4,
        "пятницу": 4,
        "пятницы": 4,
        "суббота": 5,
        "субботу": 5,
        "воскресенье": 6,
    }

    for word, idx in weekday_map.items():
        if word in t:
            dates.append(_next_weekday_from_text(t, idx))

    result = []
    for d in dates:
        if d not in result:
            result.append(d)
    return result


def _extract_target_date_for_move(text: str) -> str | None:
    t = (text or "").lower().replace("ё", "е")

    if "на пятниц" in t or "в пятниц" in t:
        return _next_weekday_from_text(t, 4)
    if "на сред" in t or "в сред" in t:
        return _next_weekday_from_text(t, 2)
    if "на понедельник" in t or "в понедельник" in t:
        return _next_weekday_from_text(t, 0)
    if "на вторник" in t or "во вторник" in t:
        return _next_weekday_from_text(t, 1)
    if "на четверг" in t or "в четверг" in t:
        return _next_weekday_from_text(t, 3)
    if "на суббот" in t or "в суббот" in t:
        return _next_weekday_from_text(t, 5)
    if "на воскрес" in t or "в воскрес" in t:
        return _next_weekday_from_text(t, 6)

    dates = _extract_ru_dates_from_text(t)
    if dates:
        return dates[-1]

    return None



def _month_range(offset_months: int = 0) -> tuple[str, str]:
    today = date.today()
    year = today.year
    month = today.month + offset_months

    while month > 12:
        month -= 12
        year += 1

    while month < 1:
        month += 12
        year -= 1

    start = date(year, month, 1)

    if month == 12:
        next_start = date(year + 1, 1, 1)
    else:
        next_start = date(year, month + 1, 1)

    end = next_start - timedelta(days=1)
    return start.isoformat(), end.isoformat()



def _clean(text: str | None) -> str:
    return (text or "").strip().lower().replace("ё", "е")




def _parse_ru_month_period(text: str | None) -> tuple[str, str] | None:
    t = _clean(text)

    month_map = {
        "январ": 1,
        "феврал": 2,
        "март": 3,
        "марте": 3,
        "апрел": 4,
        "мая": 5,
        "май": 5,
        "июн": 6,
        "июл": 7,
        "август": 8,
        "сентябр": 9,
        "октябр": 10,
        "ноябр": 11,
        "декабр": 12,
    }

    month = None
    for key, value in month_map.items():
        if key in t:
            month = value
            break

    if month is None:
        return None

    today = date.today()
    year = today.year

    # If user asks for a month that already passed this year,
    # assume next year only for future/planning contexts.
    # For current tests on May 2026: июнь => 2026-06.
    if month < today.month and any(x in t for x in ["следующ", "будущ"]):
        year += 1

    start = date(year, month, 1)

    if month == 12:
        next_start = date(year + 1, 1, 1)
    else:
        next_start = date(year, month + 1, 1)

    end = next_start - timedelta(days=1)
    return start.isoformat(), end.isoformat()

def _parse_copy_selected_workout_action(text: str | None) -> dict | None:
    t = _clean(text)

    if not any(x in t for x in ["скоп", "копир", "дублир", "продублир", "повтори", "поставь такую же"]):
        return None

    has_workout_reference = any(
        x in t for x in ["трениров", "трени", "треньк", "занятие", "эту", "такую же", "ее", "её"]
    )

    has_target_reference = any(
        x in t for x in [
            "следующую неделю",
            "следующей неделе",
            "неделю",
            "следующий месяц",
            "следующем месяце",
            "весь месяц",
            "месяц",
            "понедельник",
            "понедельники",
            "вторник",
            "вторники",
            "сред",
            "четверг",
            "пятниц",
            "суббот",
            "воскрес",
            "пн",
            "вт",
            "ср",
            "чт",
            "пт",
            "сб",
            "вс",
        ]
    )

    # Live UX:
    # after a workout was shown/created, user can say just
    # “скопируй на следующую неделю” / “продублируй на весь месяц”.
    # Source workout will be taken from selected context by executor.
    if not has_workout_reference and not has_target_reference:
        return None

    action = {
        "action": "copy_workout",
        "confidence": 0.93,
        "source": "selected_context",
        "source_date": None,
        "target_date": None,
        "target_dates": None,
        "copy_mode": "single",
        "skip_existing": True,
        "summary": "Скопировать выбранную плановую тренировку",
    }

    # Single copy: selected workout + 7 days.
    if "следующую неделю" in t or "на неделю вперед" in t or "на неделю вперёд" in t:
        action["target_rule"] = "source_plus_7_days"
        return action

    # Recurring same weekday by source workout weekday.
    if "следующий месяц" in t or "следующем месяце" in t:
        action["copy_mode"] = "recurring"
        action["target_rule"] = "next_month_same_weekday"
        return action

    if "весь месяц" in t:
        action["copy_mode"] = "recurring"
        action["target_rule"] = "months_same_weekday"
        action["months"] = 1
        return action

    if "месяц" in t:
        action["copy_mode"] = "recurring"
        action["target_rule"] = "months_same_weekday"

        # на два месяца / на 2 месяца
        if "два месяц" in t:
            action["months"] = 2
        else:
            import re
            m = re.search(r"(\d+)\s*месяц", t)
            action["months"] = int(m.group(1)) if m else 1

        return action

    # Explicit weekday recurring: следующие понедельники / по средам / каждый вторник.
    weekday_map = {
        "понедельник": 0, "понедельникам": 0, "понедельники": 0, "пн": 0,
        "вторник": 1, "вторникам": 1, "вторники": 1, "вт": 1,
        "сред": 2, "средам": 2, "среды": 2, "ср": 2,
        "четверг": 3, "четвергам": 3, "четверги": 3, "чт": 3,
        "пятниц": 4, "пятницам": 4, "пятницы": 4, "пт": 4,
        "суббот": 5, "субботам": 5, "субботы": 5, "сб": 5,
        "воскрес": 6, "воскресеньям": 6, "воскресенья": 6, "вс": 6,
    }

    explicit_weekday = None
    for key, value in weekday_map.items():
        if key in t:
            explicit_weekday = value
            break

    if explicit_weekday is not None and any(x in t for x in ["следующ", "кажд", "по ", "все "]):
        action["copy_mode"] = "recurring"
        action["target_rule"] = "next_weekdays"
        action["weekday"] = explicit_weekday

        import re
        m = re.search(r"следующ(?:ие|их)?\s+(\d+)", t)
        if not m:
            m = re.search(r"(\d+)\s+(?:понедельник|вторник|сред|четверг|пятниц|суббот|воскрес)", t)

        action["count"] = int(m.group(1)) if m else 4
        return action

    return None

def fast_parse_planning_action(text: str) -> dict | None:
    """
    Deterministic shortcut layer.
    It is intentionally small. If user says something slightly different,
    AI parser should handle it.
    """
    t = (text or "").strip().lower().replace("ё", "е")

    if not t:
        return None

    copy_action = _parse_copy_selected_workout_action(text)
    if copy_action:
        return copy_action

    # Delete/cancel planned workouts has priority over show-planned intents.
    # Example: “удали запланированные тренировки” must not be parsed as “show planned workouts”.
    if any(x in t for x in ["удали", "удалить", "удаляй", "отмени", "отменить", "отменяй", "снеси", "сноси"]) and any(
        x in t for x in ["трениров", "трени", "треньк", "тренечк", "планов", "запланирован"]
    ):
        scope = "all"
        start_date = None
        end_date = None

        month_period = _parse_ru_month_period(t)
        dates = _extract_ru_dates_from_text(t) if "_extract_ru_dates_from_text" in globals() else []

        if month_period:
            scope = "period"
            start_date, end_date = month_period
        elif len(dates) >= 2:
            scope = "period"
            start_date = min(dates)
            end_date = max(dates)
        elif len(dates) == 1:
            scope = "period"
            start_date = dates[0]
            end_date = dates[0]
        elif "следующ" in t and "недел" in t:
            scope = "next_week"
        elif "текущ" in t and "недел" in t:
            scope = "current_week"
        elif "будущ" in t or "запланирован" in t:
            scope = "future"

        return {
            "action": "cancel_planned_workouts",
            "confidence": 0.99,
            "scope": scope,
            "start_date": start_date,
            "end_date": end_date,
            "affects": "planned_only",
            "requires_confirmation": True,
            "summary": "Отменить активные плановые тренировки",
        }

    # Next month / current month plan.
    if "план" in t and "месяц" in t:
        if "следующ" in t:
            start_date, end_date = _month_range(1)
        else:
            start_date, end_date = _month_range(0)

        return {
            "action": "show_period_plan",
            "confidence": 0.94,
            "scope": "period",
            "start_date": start_date,
            "end_date": end_date,
            "include_archive": False,
            "summary": "Показать активный план на месяц",
        }

    # Explicit active planned workouts.
    if any(x in t for x in ["запланирован", "будущ", "что по плану", "что стоит", "впереди"]) and "трениров" in t:
        return {
            "action": "show_period_plan",
            "confidence": 0.94,
            "scope": "future",
            "include_archive": False,
            "summary": "Показать активные запланированные тренировки",
        }

    # Date-only follow-up: “на 10 мая”
    if t.startswith("на "):
        dates = _extract_ru_dates_from_text(t)
        if len(dates) == 1:
            return {
                "action": "show_workout_on_date",
                "confidence": 0.88,
                "target_date": dates[0],
                "include_weights": False,
                "summary": "Показать тренировку на указанную дату",
            }

    # Move planned workout. This must win over edit/reorder logic.
    if "перенеси" in t and ("трениров" in t or " ее" in t or " её" in t or " эту" in t):
        dates = _extract_ru_dates_from_text(t)
        target_date = _extract_target_date_for_move(t)

        source_date = None
        if len(dates) >= 2:
            source_date = dates[0]
            target_date = dates[1]
        elif "со сред" in t or "с сред" in t:
            source_date = _next_weekday_from_text(t, 2)
        elif "с понедельник" in t:
            source_date = _next_weekday_from_text(t, 0)

        if target_date:
            return {
                "action": "move_workout",
                "confidence": 0.96,
                "source_date": source_date,
                "target_date": target_date,
                "summary": "Перенести плановую тренировку",
            }

    # Show all upcoming workouts.
    if ("следующ" in t or "будущ" in t) and "трениров" in t and ("все" in t or "следующие" in t):
        return {
            "action": "show_period_plan",
            "confidence": 0.94,
            "scope": "future",
            "summary": "Показать все будущие плановые тренировки",
        }

    # Multi-date / period show.
    if any(x in t for x in ["покажи", "дай", "выведи"]) and "трениров" in t:
        dates = _extract_ru_dates_from_text(t)
        if len(dates) >= 2:
            return {
                "action": "show_period_plan",
                "confidence": 0.93,
                "scope": "period",
                "start_date": min(dates),
                "end_date": max(dates),
                "summary": "Показать тренировки за период",
            }

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

    # Planned cancellation with clean fitness vocabulary.
    has_delete_word = _contains_any(t, DELETE_TERMS)
    has_workout_object = _has_workout_object(t)

    if has_delete_word and has_workout_object:
        scope = "all"
        start_date = None
        end_date = None

        dates = _extract_ru_dates_from_text(t)
        if len(dates) >= 2:
            scope = "period"
            start_date = min(dates)
            end_date = max(dates)
        elif len(dates) == 1:
            scope = "period"
            start_date = dates[0]
            end_date = dates[0]
        elif "следующ" in t and "недел" in t:
            scope = "next_week"
        elif "текущ" in t and "недел" in t:
            scope = "current_week"
        elif "от сегодня" in t or "начиная с сегодня" in t or "будущ" in t:
            scope = "future"

        return {
            "action": "cancel_planned_workouts",
            "confidence": 0.97,
            "scope": scope,
            "start_date": start_date,
            "end_date": end_date,
            "affects": "planned_only",
            "requires_confirmation": True,
            "summary": "Отменить активные плановые тренировки",
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

Фитнес-лексика и разговорные формы:
- "тренировка", "тренировки", "треня", "треню", "трени", "тренька", "треньку", "треньк", "треньки", "треньке", "тренечку" = тренировка.
- "занятие", "занятия", "воркаут", "workout", "зал" могут означать тренировку, если контекст про спорт.
- "план тренировок", "тренировочный план", "программа тренировок", "прога" = плановые тренировки.

Удаление/отмена планов:
- "удали", "убери", "отмени", "снеси", "очисти", "почисти", "обнули", "сбрось", "сними" + фитнес-объект
  => cancel_planned_workouts, planned_only=true.
- "начать заново", "начнём заново", "перезапусти план", "пересобери план"
  => cancel_planned_workouts, scope=all, planned_only=true, requires_confirmation=true.

Создание:
- "добавь", "создай", "запланируй", "поставь", "запиши", "накидай", "собери", "составь", "сделай",
  "хочу потренироваться", "давай добавим" + фитнес-объект
  => create_custom_workout.

Важно:
- dev-слова типа "деплой", "коммит", "терминал", "код", "скрипт", "чекни терминал" НЕ являются фитнес-действиями.

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
