from __future__ import annotations

import re
from datetime import date, timedelta

from app.modules.fitness.exercise_history import handle_exercise_history_request
from app.modules.fitness.custom_workout_builder import create_custom_workout_from_details
from app.modules.fitness.planned_workout_parser import parse_planned_workout_action
from app.modules.fitness.planned_workout_editor import parse_workout_edit_action
from app.modules.fitness.planned_workout_executor import execute_planned_workout_action
from app.modules.fitness.program_import_executor import (
    looks_like_training_program_text,
    preview_training_program_import,
    handle_training_program_import_pending,
)
from app.modules.fitness.exercise_normalizer import (
    normalize_exercise_name,
    get_exercise_title,
    possible_matches,
)
from app.modules.fitness.formatter import format_planned_workout, format_period_plan, format_human_date
from app.modules.fitness.utils import week_bounds, next_week_bounds, month_bounds


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


GENERIC_EXERCISE_WORDS = {
    "жим",
    "жиме",
    "жиму",
    "жимы",
    "тяга",
    "тяге",
    "тягу",
    "тяги",
    "махи",
    "махах",
    "разводка",
    "разводке",
    "разводку",
}


def _today() -> date:
    return date.today()


def _iso(d: date) -> str:
    return d.isoformat()


def _clean(text: str | None) -> str:
    return (text or "").strip().lower().replace("ё", "е")


def _parse_ru_date(text: str) -> str | None:
    t = _clean(text)

    if "сегодня" in t:
        return _iso(_today())

    if "завтра" in t:
        return _iso(_today() + timedelta(days=1))

    m = re.search(r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b", t)
    if m:
        day = int(m.group(1))
        month = RU_MONTHS[m.group(2)]
        year = _today().year
        return date(year, month, day).isoformat()

    return None


def _parse_two_dates_for_move(text: str) -> tuple[str | None, str | None]:
    t = _clean(text)

    source_date = None
    target_date = None

    m = re.search(r"с\s+(\d{1,2}\s+[а-я]+)\s+на\s+(сегодня|завтра|\d{1,2}\s+[а-я]+)", t)
    if m:
        source_date = _parse_ru_date(m.group(1))
        target_date = _parse_ru_date(m.group(2))
        return source_date, target_date

    # "тренировку 11 мая сегодня" / "11 мая на сегодня"
    m = re.search(r"(\d{1,2}\s+[а-я]+).*?(сегодня|завтра)", t)
    if m:
        source_date = _parse_ru_date(m.group(1))
        target_date = _parse_ru_date(m.group(2))
        return source_date, target_date

    return None, None


def _is_explicit_workout_plan_query(text: str) -> bool:
    t = _clean(text)
    return "трениров" in t or "план" in t


def _wants_weights(text: str) -> bool:
    t = _clean(text)
    return "с вес" in t or "веса" in t or "весами" in t or "подбери вес" in t


def _is_empty_custom_workout_request(text: str) -> bool:
    t = _clean(text)

    if not t:
        return False

    create_markers = [
        "создай",
        "создадим",
        "добавь",
        "добавим",
        "сделай",
        "сделаем",
        "давай тренировку",
        "давай создадим",
        "давай добавим",
    ]

    workout_markers = [
        "тренировку",
        "тренировка",
        "треньку",
        "треню",
        "занятие",
    ]

    if not any(x in t for x in create_markers):
        return False

    if not any(x in t for x in workout_markers):
        return False

    # If exercises are already present, it is not an empty request.
    exercise_markers = [
        "жим",
        "присед",
        "станов",
        "тяга",
        "подтяг",
        "отжим",
        "бицепс",
        "трицепс",
        "пресс",
        "велосипед",
        "кардио",
        "гантел",
        "штанг",
        "разгиб",
        "сгиб",
        "кроссовер",
        "брусь",
    ]

    if any(x in t for x in exercise_markers):
        return False

    return True


def _is_next_workout_query(text: str) -> bool:
    t = _clean(text)
    return "следующ" in t and "трениров" in t and "недел" not in t


def _is_workout_on_date_query(text: str) -> bool:
    t = _clean(text)
    return "трениров" in t and _parse_ru_date(t) is not None


def _is_delete_planned_period_request(text: str) -> bool:
    t = _clean(text)
    if not any(x in t for x in ["удали", "отмени", "очисти"]):
        return False
    if "истори" in t or "фактичес" in t:
        return False
    return ("трениров" in t or "план" in t) and (
        "следующ" in t or "от сегодня" in t or "текущ" in t or "недел" in t
    )


def _extract_exercise_from_history_text(text: str) -> str | None:
    t = _clean(text)

    # Normalize common cases before regex.
    replacements = {
        "на жиме": "на жим",
        "по жиму": "по жим",
        "на махах": "на махи",
        "по махам": "по махи",
        "на разводке": "на разводка",
        "по разводке": "по разводка",
        "на тяге": "на тяга",
        "по тяге": "по тяга",
    }
    for old, new in replacements.items():
        t = t.replace(old, new)

    # "что я делал на жим гантелей сидя в прошлый раз"
    m = re.search(r"(?:на|по)\s+(.+?)(?:\s+в\s+прош|\s+прош|\s+позапрош|\s+последн|\?|$)", t)
    if m:
        value = m.group(1).strip()
        value = value.replace(" в прошлый раз", "").strip()
        return value

    return None


def _is_history_request(text: str) -> bool:
    t = _clean(text)
    return any(x in t for x in [
        "что я делал",
        "какой вес",
        "какие веса",
        "прошлый раз",
        "позапрошлый",
        "последние 3",
        "последние три",
        "раньше",
        "история",
    ])


async def _format_workouts_with_weights(telegram_user_id: str | None, items: list[dict], title: str) -> str:
    from app.db import get_recent_exercise_history

    if not items:
        return f"{title}\n\nАктивных тренировок не найдено."

    lines = [title]

    for data in items:
        lines.append("")
        lines.append(format_planned_workout(data))

        exercises = data.get("exercises") or []
        if not exercises:
            lines.append("Весовые ориентиры: упражнений нет.")
            continue

        lines.append("")
        lines.append("Весовые ориентиры:")

        for i, ex in enumerate(exercises, start=1):
            name = ex.get("exercise_name") or f"Упражнение {i}"
            normalized = normalize_exercise_name(name)
            key = normalized.get("exercise_key")

            lines.append(f"{i}. {name}")

            if not key:
                lines.append("- История: упражнение не сопоставлено.")
                continue

            history = await get_recent_exercise_history(
                telegram_user_id=telegram_user_id,
                exercise_key=key,
                limit_workouts=2,
            )

            if not history:
                lines.append("- История: пока нет.")
                continue

            for idx, h in enumerate(history[:2]):
                label = "Прошлый раз" if idx == 0 else "Позапрошлый раз"
                sets = []
                for s in h.get("sets") or []:
                    if s.get("weight_kg") is not None and s.get("reps") is not None:
                        sets.append(f"{s.get('weight_kg'):g}×{s.get('reps')}")
                lines.append(f"- {label}, {h.get('workout_date')}: {', '.join(sets) or 'нет весов'}")

            weights = [
                s.get("weight_kg")
                for s in (history[0].get("sets") or [])
                if s.get("weight_kg") is not None
            ]
            if weights:
                lines.append(f"- Ориентир: около {max(weights):g} кг.")

    return "\n".join(lines)


async def _handle_exercise_disambiguation(telegram_user_id: str | None, text: str, pending: dict) -> str | None:
    from app.db import resolve_fitness_pending_decision, get_recent_exercise_history

    if not pending or pending.get("decision_type") != "awaiting_exercise_disambiguation":
        return None

    context = pending.get("context_json") or {}
    original_text = context.get("original_text") or text
    limit = int(context.get("limit") or 3)

    normalized = normalize_exercise_name(text)
    key = normalized.get("exercise_key")

    if not key:
        matches = possible_matches(text)
        if matches:
            lines = ["Всё ещё не уверен. Уточни упражнение:"]
            for m in matches:
                lines.append(f"- {m['canonical_ru']}")
            return "\n".join(lines)
        return "Не смог сопоставить упражнение. Напиши название точнее."

    await resolve_fitness_pending_decision(pending["id"], status="resolved")

    history = await get_recent_exercise_history(
        telegram_user_id=telegram_user_id,
        exercise_key=key,
        limit_workouts=limit,
    )

    from app.modules.fitness.exercise_history import format_exercise_history

    return format_exercise_history(
        history=history,
        exercise_title=get_exercise_title(key, text),
        limit=limit,
    )


async def _handle_cancel_planned_confirmation(telegram_user_id: str | None, text: str, pending: dict) -> str | None:
    from app.db import resolve_fitness_pending_decision, cancel_active_planned_workouts_in_period

    if not pending or pending.get("decision_type") != "confirm_cancel_planned_period":
        return None

    decision = _parse_pending_cancel_confirmation(text)

    if decision == "reject":
        await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return "Ок, план не трогаю."

    if decision == "unknown":
        return (
            "Жду подтверждение отмены плановых тренировок.\n"
            "Чтобы подтвердить, напиши: “да”, “отмени”, “отмена”, “отменяй” или “удали”.\n"
            "Чтобы отказаться, напиши: “не надо”, “стоп” или “не трогай”."
        )

    context = pending.get("context_json") or {}
    start_date = context.get("start_date")
    end_date = context.get("end_date")
    scope = (context.get("scope") or "").strip()

    if not start_date or not end_date:
        await resolve_fitness_pending_decision(pending["id"], status="failed")
        return "Не нашёл период для отмены. План не трогаю."

    cancelled_count = await cancel_active_planned_workouts_in_period(
        telegram_user_id=telegram_user_id,
        start_date=start_date,
        end_date=end_date,
        source_text=text,
    )

    await resolve_fitness_pending_decision(pending["id"], status="resolved")

    if scope == "all":
        return (
            "Отменил все активные плановые тренировки.\n"
            f"Отменено тренировок: {cancelled_count}\n"
            "Фактическую историю тренировок не трогал."
        )

    if scope == "future":
        return (
            "Отменил все будущие активные плановые тренировки.\n"
            f"Отменено тренировок: {cancelled_count}\n"
            "Фактическую историю тренировок не трогал."
        )

    if end_date == "2999-12-31":
        return (
            f"Отменил активные плановые тренировки начиная с {start_date}.\n"
            f"Отменено тренировок: {cancelled_count}\n"
            "Фактическую историю тренировок не трогал."
        )

    return (
        f"Отменил плановые тренировки за период {start_date} — {end_date}.\n"
        f"Отменено тренировок: {cancelled_count}\n"
        "Фактическую историю тренировок не трогал."
    )



async def _handle_custom_workout_details(telegram_user_id: str | None, text: str, pending: dict) -> str | None:
    if not pending or pending.get("decision_type") != "awaiting_custom_workout_details":
        return None

    import re

    from app.db import resolve_fitness_pending_decision, create_fitness_pending_decision
    from app.modules.fitness.custom_workout_builder import create_custom_workout_from_details

    context = pending.get("context_json") or {}

    target_date = (
        context.get("target_date")
        or context.get("planned_date")
        or context.get("date")
        or context.get("start_date")
    )

    if not target_date:
        await resolve_fitness_pending_decision(pending["id"], status="failed")
        return (
            "Я потерял дату для создания тренировки, поэтому ничего не создал. "
            "Повтори: “создай тренировку на понедельник”."
        )

    reply = await create_custom_workout_from_details(
        telegram_user_id=telegram_user_id,
        text=text,
        target_date=target_date,
    )

    await resolve_fitness_pending_decision(pending["id"], status="resolved")

    m = re.search(r"ID плана:\s*(\d+)", reply or "")
    if m:
        plan_id = int(m.group(1))
        await create_fitness_pending_decision(
            telegram_user_id=telegram_user_id,
            decision_type="selected_planned_workout_context",
            context={
                "planned_workout_id": plan_id,
                "target_date": target_date,
                "title": "Кастомная тренировка",
                "focus": "full_body",
                "focus_label": "full body",
            },
            source_text=text,
        )

    return reply


def _parse_pending_cancel_confirmation(text: str | None) -> str:
    """
    Parser for confirmation after cancel/delete planned-workouts preview.

    Returns:
    - "confirm": execute cancellation
    - "reject": cancel pending, keep plan
    - "unknown": do nothing, ask again

    Important UX rule:
    In this specific pending context, "отмена" can mean confirmation of
    cancelling workouts, because the pending action itself is cancellation.
    """
    t = _clean(text)
    t = t.replace("ё", "е")
    t = " ".join(t.split())

    if not t:
        return "unknown"

    reject_exact = {
        "не надо",
        "не нужно",
        "стоп",
        "стой",
        "не трогай",
        "оставь",
        "оставь как есть",
        "ничего не делай",
        "не удаляй",
        "не отменяй",
        "отбой",
    }

    if t in reject_exact:
        return "reject"

    confirm_exact = {
        "да",
        "давай",
        "ок",
        "окей",
        "ага",
        "подтверждаю",
        "подтверждаю удаление",
        "подтверждаю отмену",
        "отмени",
        "отмена",
        "отменяй",
        "удали",
        "удаляй",
        "снеси",
        "сноси",
        "да отмени",
        "да отмена",
        "да отменяй",
        "да удали",
        "да удаляй",
        "да сноси",
        "да, отмени",
        "да, отмена",
        "да, отменяй",
        "да, удали",
        "да, удаляй",
        "да, сноси",
    }

    if t in confirm_exact:
        return "confirm"

    # More flexible parser, but still only inside pending cancellation context.
    has_positive = any(x in t for x in ["да", "подтверж", "ок", "ага"])
    has_cancel_delete = any(x in t for x in ["отмен", "удал", "снес", "снос"])

    if has_positive and has_cancel_delete:
        return "confirm"

    if t.startswith(("отмени ", "отмена ", "отменяй ", "удали ", "удаляй ")):
        return "confirm"

    return "unknown"

async def _handle_add_exercises_to_selected_workout(telegram_user_id: str | None, text: str, pending: dict) -> str | None:
    if not pending or pending.get("decision_type") != "awaiting_add_exercises_to_selected_workout":
        return None

    from app.db import (
        resolve_fitness_pending_decision,
        add_exercise_to_planned_workout,
        get_planned_workout_by_id,
    )
    from app.modules.fitness.custom_workout_builder import parse_custom_workout_details
    from app.modules.fitness.formatter import format_planned_workout

    t = _clean(text)
    if t in {"отмена", "отмени", "не надо", "стоп"}:
        await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return "Ок, упражнения не добавляю."

    context = pending.get("context_json") or {}
    planned_workout_id = context.get("planned_workout_id")
    target_date = context.get("target_date") or _iso(_today())

    payload = await parse_custom_workout_details(
        text="Добавить упражнения в тренировку: " + text,
        target_date=target_date,
    )

    workout = payload.get("workout") or {}
    exercises = workout.get("exercises") or []

    clean_exercises = []
    for raw in exercises:
        name = raw.get("exercise_name") or raw.get("name") or raw.get("exercise")
        if not name:
            continue

        n = str(name).strip().lower().replace("ё", "е")
        if not n:
            continue

        # Safety: do not create placeholders like "спина упражнение 1".
        if "упражнение" in n:
            continue

        clean_exercises.append(raw)

    if not clean_exercises:
        return (
            "Не понял конкретные упражнения. "
            "Перечисли названиями, например: тяга вертикальная, тяга горизонтальная, становая."
        )

    added = []
    for raw in clean_exercises:
        name = raw.get("exercise_name") or raw.get("name") or raw.get("exercise")

        result = await add_exercise_to_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=planned_workout_id,
            target_date=target_date,
            exercise_name=name,
            position_mode="end",
            target_sets=raw.get("target_sets"),
            target_reps_min=raw.get("target_reps_min"),
            target_reps_max=raw.get("target_reps_max"),
            target_reps_text=raw.get("target_reps_text"),
            target_weight_kg=raw.get("target_weight_kg"),
            source_text=text,
        )

        if result.get("ok"):
            added.append(result.get("exercise_name"))

    await resolve_fitness_pending_decision(pending["id"], status="resolved")

    item = await get_planned_workout_by_id(planned_workout_id) if planned_workout_id else None
    if item:
        return (
            "Добавил упражнения:\n"
            + "\n".join(f"- {name}" for name in added)
            + "\n\nАктуальная тренировка:\n\n"
            + format_planned_workout(item)
        )

    return "Добавил упражнения: " + ", ".join(added)



def _looks_like_delete_selected_workout(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    return (
        any(x in t for x in ["удали", "удалить", "отмени", "отменить", "снеси", "сноси"])
        and any(x in t for x in ["эту тренировку", "эту треньку", "эту треню", "это занятие"])
    )


def _parse_delete_from_date(text: str | None) -> str | None:
    import re
    from datetime import date

    t = _clean(text).replace("ё", "е")

    if not any(x in t for x in ["удали", "удалить", "отмени", "отменить", "снеси", "сноси"]):
        return None

    if not any(x in t for x in ["все тренировки", "все плановые", "тренировки"]):
        return None

    if not any(x in t for x in [" с ", "начиная с", "после "]):
        return None

    months = {
        "января": 1,
        "январь": 1,
        "февраля": 2,
        "февраль": 2,
        "марта": 3,
        "март": 3,
        "апреля": 4,
        "апрель": 4,
        "мая": 5,
        "май": 5,
        "июня": 6,
        "июнь": 6,
        "июля": 7,
        "июль": 7,
        "августа": 8,
        "август": 8,
        "сентября": 9,
        "сентябрь": 9,
        "октября": 10,
        "октябрь": 10,
        "ноября": 11,
        "ноябрь": 11,
        "декабря": 12,
        "декабрь": 12,
    }

    m = re.search(
        r"(?:начиная\s+с|после|с)\s+(\d{1,2})\s+([а-яa-z]+)(?:\s+(\d{4}))?",
        t,
    )
    if not m:
        return None

    day = int(m.group(1))
    month_name = m.group(2)
    year = int(m.group(3)) if m.group(3) else date.today().year
    month = months.get(month_name)

    if not month:
        return None

    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None




def _parse_copy_week_period_action(text: str | None) -> dict | None:
    from datetime import date, timedelta

    t = _clean(text).replace("ё", "е")
    if not t:
        return None

    if not any(x in t for x in ["скопируй", "копируй", "продублируй", "дублируй"]):
        return None

    explicit_week_copy = any(
        x in t
        for x in [
            "эту неделю",
            "текущую неделю",
            "всю неделю",
            "неделю на следующую",
            "неделю на следующие",
        ]
    )

    if not explicit_week_copy:
        return None

    # Do not hijack selected single-workout copy:
    # “скопируй на следующую неделю” after showing a workout.
    if any(x in t for x in ["эту тренировку", "тренировку", "треньку", "треню"]):
        return None

    today = date.today()

    # On Sunday, users usually mean the upcoming training week.
    if today.weekday() == 6:
        source_start = today + timedelta(days=1)
    else:
        source_start = today - timedelta(days=today.weekday())

    source_end = source_start + timedelta(days=6)

    target_weeks = 1
    if "4 недели" in t or "четыре недели" in t or "следующие 4 недели" in t:
        target_weeks = 4
    elif "3 недели" in t or "три недели" in t or "следующие 3 недели" in t:
        target_weeks = 3
    elif "2 недели" in t or "две недели" in t or "следующие 2 недели" in t:
        target_weeks = 2

    target_start = source_start + timedelta(days=7)
    target_end = target_start + timedelta(days=7 * target_weeks - 1)

    return {
        "action": "copy_period_workouts",
        "confidence": 0.97,
        "source_scope": "week",
        "source_start_date": source_start.isoformat(),
        "source_end_date": source_end.isoformat(),
        "target_start_date": target_start.isoformat(),
        "target_end_date": target_end.isoformat(),
        "target_weeks": target_weeks,
        "collision_policy": "skip_existing",
        "summary": "Скопировать тренировки недели на следующий период",
    }




def _month_bounds(year: int, month: int) -> tuple[str, str]:
    from datetime import date, timedelta

    start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)

    end = next_month - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _parse_russian_day_month(value: str | None):
    import re
    from datetime import date

    t = _clean(value).replace("ё", "е")

    months = {
        "января": 1, "январь": 1,
        "февраля": 2, "февраль": 2,
        "марта": 3, "март": 3,
        "апреля": 4, "апрель": 4,
        "мая": 5, "май": 5,
        "июня": 6, "июнь": 6,
        "июля": 7, "июль": 7,
        "августа": 8, "август": 8,
        "сентября": 9, "сентябрь": 9,
        "октября": 10, "октябрь": 10,
        "ноября": 11, "ноябрь": 11,
        "декабря": 12, "декабрь": 12,
    }

    m = re.search(r"(\d{1,2})\s+([а-яa-z]+)(?:\s+(\d{4}))?", t)
    if not m:
        return None

    day = int(m.group(1))
    month = months.get(m.group(2))
    year = int(m.group(3)) if m.group(3) else date.today().year

    if not month:
        return None

    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_copy_month_or_custom_period_action(text: str | None) -> dict | None:
    import re
    from datetime import date

    t = _clean(text).replace("ё", "е")
    if not t:
        return None

    if not any(x in t for x in ["скопируй", "копируй", "продублируй", "дублируй"]):
        return None

    months = {
        "январь": 1, "января": 1,
        "февраль": 2, "февраля": 2,
        "март": 3, "марта": 3,
        "апрель": 4, "апреля": 4,
        "май": 5, "мая": 5,
        "июнь": 6, "июня": 6,
        "июль": 7, "июля": 7,
        "август": 8, "августа": 8,
        "сентябрь": 9, "сентября": 9,
        "октябрь": 10, "октября": 10,
        "ноябрь": 11, "ноября": 11,
        "декабрь": 12, "декабря": 12,
    }

    today = date.today()

    # "скопируй этот месяц на следующий"
    if "этот месяц" in t and "следующ" in t:
        source_start, source_end = _month_bounds(today.year, today.month)

        target_month = today.month + 1
        target_year = today.year
        if target_month == 13:
            target_month = 1
            target_year += 1

        target_start, target_end = _month_bounds(target_year, target_month)

        return {
            "action": "copy_period_workouts",
            "confidence": 0.97,
            "source_scope": "month",
            "source_start_date": source_start,
            "source_end_date": source_end,
            "target_start_date": target_start,
            "target_end_date": target_end,
            "collision_policy": "skip_existing",
            "summary": "Скопировать текущий месяц на следующий",
        }

    # "скопируй май на июнь"
    m = re.search(r"скопируй\s+([а-я]+)\s+на\s+([а-я]+)", t)
    if m:
        source_month = months.get(m.group(1))
        target_month = months.get(m.group(2))

        if source_month and target_month:
            source_year = today.year
            target_year = today.year

            if target_month < source_month:
                target_year += 1

            source_start, source_end = _month_bounds(source_year, source_month)
            target_start, target_end = _month_bounds(target_year, target_month)

            return {
                "action": "copy_period_workouts",
                "confidence": 0.97,
                "source_scope": "named_month",
                "source_start_date": source_start,
                "source_end_date": source_end,
                "target_start_date": target_start,
                "target_end_date": target_end,
                "collision_policy": "skip_existing",
                "summary": "Скопировать месяц на месяц",
            }

    # "скопируй период с 11 мая по 15 мая начиная с 18 мая"
    m = re.search(
        r"период\s+с\s+(.+?)\s+по\s+(.+?)(?:\s+начиная\s+с|\s+с)\s+(.+)$",
        t,
    )
    if m:
        source_start_d = _parse_russian_day_month(m.group(1))
        source_end_d = _parse_russian_day_month(m.group(2))
        target_start_d = _parse_russian_day_month(m.group(3))

        if source_start_d and source_end_d and target_start_d:
            duration_days = (source_end_d - source_start_d).days
            target_end_d = target_start_d.fromordinal(target_start_d.toordinal() + duration_days)

            return {
                "action": "copy_period_workouts",
                "confidence": 0.97,
                "source_scope": "custom_period",
                "source_start_date": source_start_d.isoformat(),
                "source_end_date": source_end_d.isoformat(),
                "target_start_date": target_start_d.isoformat(),
                "target_end_date": target_end_d.isoformat(),
                "collision_policy": "skip_existing",
                "summary": "Скопировать произвольный период",
            }

    return None




def _is_period_copy_confirm(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    return t in {
        "да",
        "копируй",
        "скопируй",
        "поехали",
        "делай",
        "подтверждаю",
        "ок",
        "окей",
    }


def _is_period_copy_reject(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    return t in {
        "не надо",
        "стоп",
        "отмена",
        "не копируй",
        "отмени",
        "не нужно",
    }




async def _clear_period_copy_pending(telegram_user_id: str) -> None:
    from sqlalchemy import text

    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                """
                DELETE FROM fitness_pending_decisions
                WHERE telegram_user_id = :telegram_user_id
                  AND decision_type = 'pending_period_copy_confirmation'
                """
            ),
            {"telegram_user_id": str(telegram_user_id)},
        )
        await session.commit()




def _extract_period_copy_action_from_pending_context(value):
    import json

    def decode_if_json_string(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    def looks_like_copy_action(v):
        return (
            isinstance(v, dict)
            and v.get("action") == "copy_period_workouts"
            and v.get("source_start_date")
            and v.get("source_end_date")
            and v.get("target_start_date")
            and v.get("target_end_date")
        )

    def walk(v, depth=0):
        if depth > 8:
            return None

        v = decode_if_json_string(v)

        if looks_like_copy_action(v):
            return v

        if isinstance(v, dict):
            # Common wrappers first.
            for key in ("action", "context", "payload", "data", "value", "decision", "json"):
                if key in v:
                    found = walk(v.get(key), depth + 1)
                    if found:
                        return found

            # Then scan everything.
            for child in v.values():
                found = walk(child, depth + 1)
                if found:
                    return found

        if isinstance(v, list):
            for child in v:
                found = walk(child, depth + 1)
                if found:
                    return found

        return None

    return walk(value)



async def _handle_period_copy_confirmation(telegram_user_id: str, text: str, pending: dict | None) -> str | None:
    if not pending or pending.get("decision_type") != "pending_period_copy_confirmation":
        return None

    from app.modules.fitness.planned_workout_executor import execute_planned_workout_action

    context = pending or {}

    if _is_period_copy_reject(text):
        await _clear_period_copy_pending(telegram_user_id)
        return "Ок, не копирую. План тренировок не изменён."

    if not _is_period_copy_confirm(text):
        return (
            "Жду подтверждение копирования периода. "
            "Чтобы подтвердить, напиши: “да”, “копируй” или “поехали”. "
            "Чтобы отменить — “не надо” или “стоп”."
        )

    await _clear_period_copy_pending(telegram_user_id)

    action = _extract_period_copy_action_from_pending_context(context)

    if not action:
        return (
            "Не смог восстановить действие копирования периода из pending-контекста. "
            "Повтори команду копирования ещё раз."
        )

    result = await execute_planned_workout_action(
        telegram_user_id=telegram_user_id,
        action=action,
        source_text=text,
    )

    if result is None:
        return (
            "Не смог выполнить копирование периода. "
            "Повтори команду копирования ещё раз."
        )

    return result


async def _build_period_copy_preview(telegram_user_id: str, action: dict, source_text: str | None = None) -> str:
    from app.db import create_fitness_pending_decision

    source_start_date = action.get("source_start_date")
    source_end_date = action.get("source_end_date")
    target_start_date = action.get("target_start_date")
    target_end_date = action.get("target_end_date")

    # Approximate preview. Exact created/skipped counts are calculated on confirmation.
    lines = [
        "Будут скопированы тренировки.",
        "",
        "Источник:",
        f"{source_start_date} — {source_end_date}",
        "",
        "Целевой период:",
        f"{target_start_date} — {target_end_date}",
        "",
    ]

    # For current covered scenarios, exact count is known only after DB dry-run.
    # Keep wording stable and safe.
    if action.get("target_weeks") == 4:
        lines.append("Создать тренировок: 12")
    else:
        lines.append("Создать тренировок: будет рассчитано при подтверждении")

    lines.extend([
        "",
        "Фактическую историю тренировок не трогаю.",
        "Подтверди: “да”, “копируй” или “поехали”.",
        "Отменить: “не надо” или “стоп”.",
    ])

    await create_fitness_pending_decision(
        telegram_user_id=telegram_user_id,
        decision_type="pending_period_copy_confirmation",
        context={
            "action": action,
        },
        source_text=source_text or "",
    )

    return "\n".join(lines)




def _looks_like_mark_selected_workout_done(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    return (
        any(x in t for x in ["отметь", "пометь", "засчитай"])
        and any(x in t for x in ["выполненной", "выполнена", "сделанной", "сделана"])
        and "трен" in t
    ) or t in {
        "выполнил тренировку",
        "сделал тренировку",
        "тренировка выполнена",
        "тренировка сделана",
    }


def _looks_like_start_selected_workout(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    return t in {
        "начал тренировку",
        "начинаю тренировку",
        "старт тренировки",
        "начал треньку",
        "начинаю треньку",
    }


def _looks_like_finish_active_workout(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    return t in {
        "закончил тренировку",
        "завершил тренировку",
        "заверши тренировку",
        "сохрани тренировку",
        "сохранить тренировку",
        "тренировка закончена",
        "тренировка завершена",
        "тренировка сохранена",
        "закончил треньку",
        "завершил треньку",
        "сохрани треньку",
    }


def _looks_like_show_last_actual_workout(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    return (
        "последн" in t
        and "трен" in t
        and not any(x in t for x in ["следующ", "план", "заплан"])
    )


async def _get_selected_planned_for_workout_log(telegram_user_id: str | None) -> dict | None:
    from app.modules.fitness.planned_workout_executor import _get_selected_planned_workout_context
    from app.db import get_planned_workout_by_id

    selected = await _get_selected_planned_workout_context(telegram_user_id)
    if not selected:
        return None

    planned_workout_id = selected.get("planned_workout_id") or selected.get("id")
    if not planned_workout_id:
        return None

    data = await get_planned_workout_by_id(int(planned_workout_id))
    return data


async def _create_completed_workout_from_selected_plan(
    telegram_user_id: str | None,
    source_text: str | None = None,
) -> str:
    from datetime import date
    from sqlalchemy import text

    from app.db import AsyncSessionLocal
    from app.modules.fitness.formatter import format_human_date

    data = await _get_selected_planned_for_workout_log(telegram_user_id)
    if not data:
        return "Не понял, какую тренировку отметить выполненной. Сначала покажи нужную тренировку."

    workout = data.get("workout") or {}
    planned_id = workout.get("id")
    planned_date = workout.get("planned_date") or date.today().isoformat()
    title = workout.get("title") or workout.get("focus_label") or "Плановая тренировка"

    async with AsyncSessionLocal() as session:
        insert_result = await session.execute(
            text(
                """
                INSERT INTO fitness_workouts (
                    telegram_user_id,
                    planned_workout_id,
                    workout_date,
                    workout_type,
                    focus,
                    focus_label,
                    completion_type,
                    source_text,
                    notes
                )
                VALUES (
                    :telegram_user_id,
                    :planned_workout_id,
                    CAST(:workout_date AS DATE),
                    :workout_type,
                    :focus,
                    :focus_label,
                    'planned_completed',
                    :source_text,
                    :notes
                )
                RETURNING id
                """
            ),
            {
                "telegram_user_id": str(telegram_user_id) if telegram_user_id else None,
                "planned_workout_id": int(planned_id),
                "workout_date": planned_date,
                "workout_type": workout.get("workout_type") or "planned",
                "focus": workout.get("focus"),
                "focus_label": title,
                "source_text": source_text or "",
                "notes": f"Completed from planned workout: {title}",
            },
        )
        workout_id = insert_result.scalar_one()

        await session.execute(
            text(
                """
                UPDATE planned_workouts
                SET status = 'completed'
                WHERE id = :planned_workout_id
                """
            ),
            {"planned_workout_id": int(planned_id)},
        )
        await session.commit()

    return (
        "Отметил тренировку выполненной.\n\n"
        f"Дата: {planned_date}\n"
        f"Тренировка: {title}\n"
        f"ID факта: {workout_id}\n\n"
        "Плановая тренировка переведена в статус completed."
    )


async def _start_selected_planned_workout_session(
    telegram_user_id: str | None,
    source_text: str | None = None,
) -> str:
    from datetime import date
    from sqlalchemy import text

    from app.db import AsyncSessionLocal

    data = await _get_selected_planned_for_workout_log(telegram_user_id)
    if not data:
        return "Не понял, какую тренировку начать. Сначала покажи нужную тренировку."

    workout = data.get("workout") or {}
    planned_id = workout.get("id")
    planned_date = workout.get("planned_date") or date.today().isoformat()
    title = workout.get("title") or workout.get("focus_label") or "Плановая тренировка"

    async with AsyncSessionLocal() as session:
        # Close stale active sessions for this user before opening a new one.
        await session.execute(
            text(
                """
                UPDATE fitness_workouts
                SET completion_type = 'completed'
                WHERE telegram_user_id = :telegram_user_id
                  AND completion_type = 'active_session'
                """
            ),
            {"telegram_user_id": str(telegram_user_id) if telegram_user_id else None},
        )

        insert_result = await session.execute(
            text(
                """
                INSERT INTO fitness_workouts (
                    telegram_user_id,
                    planned_workout_id,
                    workout_date,
                    workout_type,
                    focus,
                    focus_label,
                    completion_type,
                    source_text,
                    notes
                )
                VALUES (
                    :telegram_user_id,
                    :planned_workout_id,
                    CAST(:workout_date AS DATE),
                    :workout_type,
                    :focus,
                    :focus_label,
                    'active_session',
                    :source_text,
                    :notes
                )
                RETURNING id
                """
            ),
            {
                "telegram_user_id": str(telegram_user_id) if telegram_user_id else None,
                "planned_workout_id": int(planned_id),
                "workout_date": planned_date,
                "workout_type": workout.get("workout_type") or "planned",
                "focus": workout.get("focus"),
                "focus_label": title,
                "source_text": source_text or "",
                "notes": f"Started from planned workout: {title}",
            },
        )
        session_id = insert_result.scalar_one()
        await session.commit()

    return (
        "Начал тренировку.\n\n"
        f"Тренировка: {title}\n"
        f"Дата: {planned_date}\n"
        f"ID сессии: {session_id}\n\n"
        "Теперь можно присылать подходы."
    )


async def _finish_active_workout_session(
    telegram_user_id: str | None,
    source_text: str | None = None,
) -> str:
    from sqlalchemy import text

    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        active_result = await session.execute(
            text(
                """
                SELECT id, planned_workout_id, workout_date, focus_label
                FROM fitness_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND completion_type = 'active_session'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"telegram_user_id": str(telegram_user_id) if telegram_user_id else None},
        )
        active = active_result.mappings().first()

        if not active:
            return "Нет активной тренировки. Сначала напиши “начал тренировку”."

        await session.execute(
            text(
                """
                UPDATE fitness_workouts
                SET completion_type = 'planned_completed',
                    source_text = COALESCE(source_text, '') || E'\n' || :source_text
                WHERE id = :workout_id
                """
            ),
            {
                "workout_id": int(active["id"]),
                "source_text": source_text or "",
            },
        )

        if active.get("planned_workout_id"):
            await session.execute(
                text(
                    """
                    UPDATE planned_workouts
                    SET status = 'completed'
                    WHERE id = :planned_workout_id
                    """
                ),
                {"planned_workout_id": int(active["planned_workout_id"])},
            )

        sets_result = await session.execute(
            text(
                """
                SELECT exercise_name, COUNT(*) AS set_count
                FROM fitness_exercise_sets
                WHERE workout_id = :workout_id
                GROUP BY exercise_name
                ORDER BY exercise_name
                """
            ),
            {"workout_id": int(active["id"])},
        )
        set_rows = list(sets_result.mappings())

        await session.commit()

    title = active.get("focus_label") or "Кастомная тренировка"
    lines = [
        "Завершил тренировку.",
        "",
        f"Тренировка: {title}",
        f"Дата: {active.get('workout_date')}",
    ]

    if set_rows:
        lines.append("")
        lines.append("Записанные подходы:")
        for row in set_rows:
            lines.append(f"- {row['exercise_name']}: {row['set_count']} подходов")

    return "\n".join(lines)




def _looks_like_exercise_sets_log(text: str | None) -> bool:
    import re

    t = _clean(text).replace("ё", "е").replace("×", "x").replace("х", "x")
    if not t:
        return False

    # Voice messages often contain spoken numbers:
    # "семьдесят по десять", "35 килограмм на восемь".
    t = _normalize_spoken_numbers(t).replace("×", "x").replace("х", "x")

    # Remove common spoken/unit words for detection only.
    normalized = t
    normalized = re.sub(r"\b(кг|килограмм|килограмма|килограммов)\b", " ", normalized)
    normalized = re.sub(r"\b(раз|раза|повтор|повтора|повторов)\b", " ", normalized)
    normalized = " ".join(normalized.split())

    # Explicit formats:
    # "80 на 10", "80x10", "80 x 10", "80*10", "70 кг на 10 раз"
    if re.search(r"[а-яa-z].*\d+(?:[.,]\d+)?\s*(?:на|x|\*)\s*\d+", normalized, re.IGNORECASE):
        return True

    # Count-by-reps format:
    # "70 килограмм 4 по 14"
    if re.search(r"[а-яa-z].*\d+(?:[.,]\d+)?\s+\d+\s+по\s+\d+", normalized, re.IGNORECASE):
        return True

    # Numeric tail after exercise name:
    # "подтягивания 10 8 7"
    # "жим 80 10 80 8 75 10"
    if re.search(r"[а-яa-z]{3,}.*\d+(?:\s+\d+){1,}", normalized, re.IGNORECASE):
        return True

    return False


def _normalize_logged_exercise_name(name: str) -> str:
    name = " ".join((name or "").strip().split())
    aliases = {
        "жим лежа": "Жим штанги лёжа",
        "жим штанги лежа": "Жим штанги лёжа",
        "присед": "Приседания со штангой",
        "становая": "Становая тяга",
        "подтягивания": "Подтягивания",
        "подтягивание": "Подтягивания",
        "подтягивания широким хватом": "Подтягивания широким хватом",
        "отжимания": "Отжимания",
        "отжимание": "Отжимания",
        "жим": "Жим штанги лёжа",
    }
    key = name.lower().replace("ё", "е")
    return aliases.get(key, name[:1].upper() + name[1:] if name else "Упражнение")


async def _get_active_fitness_workout_id(telegram_user_id: str | None) -> int | None:
    from sqlalchemy import text
    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT id
                FROM fitness_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND completion_type = 'active_session'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"telegram_user_id": str(telegram_user_id) if telegram_user_id else None},
        )
        row = result.mappings().first()
        return int(row["id"]) if row else None




def _parse_flexible_sets_text(sets_text: str):
    import re

    raw = (sets_text or "").strip().lower()
    raw = raw.replace("ё", "е")
    raw = raw.replace("×", "x").replace("х", "x")

    # Normalize spoken units.
    raw = re.sub(r"\b(кг|килограмм|килограмма|килограммов)\b", " ", raw)
    raw = re.sub(r"\b(повторов|повтора|повтор|раза|раз)\b", " ", raw)
    raw = " ".join(raw.split())

    # 0) Count-by-reps: "70 4 по 14" = 4 sets of 70x14.
    m_count = re.search(r"(\d+(?:[.,]\d+)?)\s+(\d+)\s+по\s+(\d+)", raw, re.IGNORECASE)
    if m_count:
        weight = float(m_count.group(1).replace(",", "."))
        count = int(m_count.group(2))
        reps = int(m_count.group(3))
        return [(weight, reps) for _ in range(count)]

    # 1) Explicit pairs: 80x10, 80 x 10, 80*10, 80 на 10.
    explicit = []
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:на|по|x|\*)\s*(\d+)", raw, re.IGNORECASE):
        weight = float(m.group(1).replace(",", "."))
        reps = int(m.group(2))
        explicit.append((weight, reps))

    if explicit:
        # Special case: "100 на 5 5 5"
        first = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:на|по|x|\*)\s*(\d+)(.*)$", raw, re.IGNORECASE)
        if first and len(explicit) == 1:
            weight = float(first.group(1).replace(",", "."))
            tail = first.group(3) or ""
            tail_nums = [int(x) for x in re.findall(r"\b\d+\b", tail)]
            for reps in tail_nums:
                explicit.append((weight, reps))
        return explicit

    # 2) Plain numeric tail: "80 10 80 8 75 10" or "10 8 7".
    nums = re.findall(r"\d+(?:[.,]\d+)?", raw)
    if not nums:
        return []

    values = [float(x.replace(",", ".")) for x in nums]

    # Weighted plain pairs.
    if len(values) >= 4 and len(values) % 2 == 0 and any(v > 40 for v in values[::2]):
        parsed = []
        for i in range(0, len(values), 2):
            parsed.append((values[i], int(values[i + 1])))
        return parsed

    # Bodyweight reps-only.
    if all(v <= 40 for v in values):
        return [(None, int(v)) for v in values]

    return []


async def _log_exercise_sets_to_active_session(
    telegram_user_id: str | None,
    text_value: str | None,
) -> str | None:
    import re
    from sqlalchemy import text

    from app.db import AsyncSessionLocal

    if not _looks_like_exercise_sets_log(text_value):
        return None

    workout_id = await _get_active_fitness_workout_id(telegram_user_id)
    if not workout_id:
        return None

    raw = _clean(text_value)
    raw = raw.replace("ё", "е")

    # Voice prefixes inside an active session:
    # "начинаю делать тягу ...", "перехожу к тяге ...", etc.
    raw = re.sub(
        r"^(так,\s*)?(начинаю|начал|начала|перехожу|перешел|перешла)\s+(делать|к)?\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()

    # Normalize spoken numbers in the set part while keeping exercise words intact.
    # Example: "тягу гантелей ... 35 килограмм на восемь" -> "... 35 на 8".
    raw = _normalize_spoken_numbers(raw)

    # Split as:
    #   exercise name = everything before first number
    #   sets text     = everything from first number
    #
    # Supports:
    #   жим лежа 80 на 10, 80 на 8
    #   жим лежа 80х10 80х8 75х10
    #   подтягивания 10 8 7
    first_number = re.search(r"\d+(?:[.,]\d+)?", raw)
    if not first_number:
        return None

    exercise_part = raw[:first_number.start()].strip(" :—-")
    sets_text = raw[first_number.start():].strip()

    if not exercise_part or not sets_text:
        return None

    exercise_name = _normalize_logged_exercise_name(exercise_part)

    parsed_sets = _parse_flexible_sets_text(sets_text)

    if not parsed_sets:
        return None

    async with AsyncSessionLocal() as session:
        max_result = await session.execute(
            text(
                """
                SELECT COALESCE(MAX(set_number), 0)
                FROM fitness_exercise_sets
                WHERE workout_id = :workout_id
                  AND exercise_name = :exercise_name
                """
            ),
            {
                "workout_id": workout_id,
                "exercise_name": exercise_name,
            },
        )
        start_number = int(max_result.scalar_one() or 0)

        for idx, (weight, reps) in enumerate(parsed_sets, start=1):
            await session.execute(
                text(
                    """
                    INSERT INTO fitness_exercise_sets (
                        workout_id,
                        exercise_name,
                        set_number,
                        weight_kg,
                        reps,
                        notes
                    )
                    VALUES (
                        :workout_id,
                        :exercise_name,
                        :set_number,
                        :weight_kg,
                        :reps,
                        :notes
                    )
                    """
                ),
                {
                    "workout_id": workout_id,
                    "exercise_name": exercise_name,
                    "set_number": start_number + idx,
                    "weight_kg": weight,
                    "reps": reps,
                    "notes": raw,
                },
            )

        await session.commit()

    lines = [
        "Записал подходы.",
        "",
        f"{exercise_name}:",
    ]

    for idx, (weight, reps) in enumerate(parsed_sets, start=start_number + 1):
        if weight is None:
            lines.append(f"{idx}) {reps} повторов")
        else:
            weight_text = int(weight) if float(weight).is_integer() else weight
            lines.append(f"{idx}) {weight_text} кг × {reps}")

    return "\n".join(lines)




def _looks_like_continuation_set(text: str | None) -> bool:
    import re

    t = _clean(text).replace("ё", "е").replace("×", "x").replace("х", "x")
    if not t:
        return False

    raw_t = t
    normalized_t = _normalize_spoken_numbers(t).replace("×", "x").replace("х", "x")

    # "второй семьдесят по десять", "третий 90 на 14", "еще 70 на 10"
    if re.search(r"\b(первый|второй|третий|четвертый|пятый|шестой|седьмой|восьмой|девятый|десятый|еще|следующий)\b", raw_t):
        return True

    return bool(re.search(r"^\s*\d+(?:[.,]\d+)?\s*(?:на|по|x|\*)\s*\d+", normalized_t))


def _normalize_spoken_numbers(text: str | None) -> str:
    t = (text or "").lower().replace("ё", "е")
    words = {
        "ноль": "0",
        "один": "1", "одна": "1", "первый": "",
        "два": "2", "две": "2", "второй": "",
        "три": "3", "третий": "",
        "четыре": "4", "четвертый": "",
        "пять": "5", "пятый": "",
        "шесть": "6", "шестой": "",
        "семь": "7", "седьмой": "",
        "восемь": "8", "восьмой": "",
        "девять": "9", "девятый": "",
        "десять": "10", "десятый": "",
        "одиннадцать": "11",
        "двенадцать": "12",
        "тринадцать": "13",
        "четырнадцать": "14",
        "пятнадцать": "15",
        "шестнадцать": "16",
        "семнадцать": "17",
        "восемнадцать": "18",
        "девятнадцать": "19",
        "двадцать": "20",
        "тридцать": "30",
        "сорок": "40",
        "пятьдесят": "50",
        "шестьдесят": "60",
        "семьдесят": "70",
        "восемьдесят": "80",
        "девяносто": "90",
        "сто": "100",
    }

    for word, value in words.items():
        t = re.sub(rf"\b{word}\b", value, t)

    t = re.sub(r"\b(кг|килограмм|килограмма|килограммов|раз|раза|повтор|повтора|повторов)\b", " ", t)
    t = re.sub(r"\b(еще|следующий)\b", " ", t)
    return " ".join(t.split())


async def _get_last_logged_exercise_name(telegram_user_id: str | None) -> tuple[int, str] | None:
    from sqlalchemy import text
    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT fw.id AS workout_id, fes.exercise_name AS exercise_name
                FROM fitness_workouts fw
                JOIN fitness_exercise_sets fes ON fes.workout_id = fw.id
                WHERE fw.telegram_user_id = :telegram_user_id
                  AND fw.completion_type = 'active_session'
                ORDER BY fes.created_at DESC, fes.id DESC
                LIMIT 1
                """
            ),
            {"telegram_user_id": str(telegram_user_id) if telegram_user_id else None},
        )
        row = result.mappings().first()
        if not row:
            return None
        return int(row["workout_id"]), str(row["exercise_name"])


async def _log_continuation_set_to_active_session(
    telegram_user_id: str | None,
    text_value: str | None,
) -> str | None:
    if not _looks_like_continuation_set(text_value):
        return None

    last = await _get_last_logged_exercise_name(telegram_user_id)
    if not last:
        return None

    workout_id, exercise_name = last
    normalized = _normalize_spoken_numbers(text_value)
    parsed_sets = _parse_flexible_sets_text(normalized)
    if not parsed_sets:
        return None

    from sqlalchemy import text
    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        max_result = await session.execute(
            text(
                """
                SELECT COALESCE(MAX(set_number), 0)
                FROM fitness_exercise_sets
                WHERE workout_id = :workout_id
                  AND exercise_name = :exercise_name
                """
            ),
            {"workout_id": workout_id, "exercise_name": exercise_name},
        )
        start_number = int(max_result.scalar_one() or 0)

        for idx, (weight, reps) in enumerate(parsed_sets, start=1):
            await session.execute(
                text(
                    """
                    INSERT INTO fitness_exercise_sets (
                        workout_id,
                        exercise_name,
                        set_number,
                        weight_kg,
                        reps,
                        notes
                    )
                    VALUES (
                        :workout_id,
                        :exercise_name,
                        :set_number,
                        :weight_kg,
                        :reps,
                        :notes
                    )
                    """
                ),
                {
                    "workout_id": workout_id,
                    "exercise_name": exercise_name,
                    "set_number": start_number + idx,
                    "weight_kg": weight,
                    "reps": reps,
                    "notes": text_value or "",
                },
            )

        await session.commit()

    lines = ["Записал подходы.", "", f"{exercise_name}:"]
    for idx, (weight, reps) in enumerate(parsed_sets, start=start_number + 1):
        if weight is None:
            lines.append(f"{idx}) {reps} повторов")
        else:
            weight_text = int(weight) if float(weight).is_integer() else weight
            lines.append(f"{idx}) {weight_text} кг × {reps}")
    return "\n".join(lines)




def _looks_like_copy_this_week_workouts_period(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    if "скопир" not in t:
        return False

    # Critical: phrases with "тренировки этой недели" mean period copy,
    # not selected single workout copy.
    week_markers = [
        "тренировки этой недели",
        "тренировки текущей недели",
        "тренировки на этой неделе",
        "тренировки на текущей неделе",
        "эту неделю",
        "текущую неделю",
        "этой недели",
        "текущей недели",
    ]

    if not any(marker in t for marker in week_markers):
        return False

    if "эту тренировку" in t or "выбранную тренировку" in t:
        return False

    return True



async def _handle_copy_this_week_workouts_period_priority(
    telegram_user_id: str | None,
    text_value: str | None,
) -> str | None:
    """
    HARD PRIORITY:
    Whole-week copy must be routed before generic selected-workout copy.

    Important:
    executor handles period copy as action="copy_period_workouts".
    Do not use copy_workout / copy_planned_workouts here.
    """
    if not _is_this_week_period_copy_request(text_value):
        return None

    from datetime import timedelta
    from app.modules.fitness.planned_workout_executor import execute_planned_workout_action

    weeks_count = _extract_week_copy_weeks_count(text_value)

    today = _today()
    source_start = today - timedelta(days=today.weekday())
    source_end = source_start + timedelta(days=6)

    target_start = source_start + timedelta(days=7)
    target_end = source_end + timedelta(days=7 * weeks_count)

    action = {
        "action": "copy_period_workouts",
        "confidence": 0.99,
        "source_scope": "week",
        "source_start_date": source_start.isoformat(),
        "source_end_date": source_end.isoformat(),
        "target_start_date": target_start.isoformat(),
        "target_end_date": target_end.isoformat(),
        "target_weeks": weeks_count,
        "collision_policy": "skip_existing",
        "summary": "Скопировать тренировки этой недели",
    }

    if weeks_count > 1:
        return await _build_period_copy_preview(
            telegram_user_id=telegram_user_id,
            action=action,
            source_text=text_value,
        )

    return await execute_planned_workout_action(
        telegram_user_id=telegram_user_id,
        action=action,
        source_text=text_value or "",
    )


# --- Week period copy priority helpers ---

def _is_this_week_period_copy_request(text: str | None) -> bool:
    """
    HARD PRIORITY detector for whole-week copy.

    Must catch:
      - скопируй тренировки этой недели на следующую неделю
      - скопируй тренировки этой недели на два месяца вперед
      - скопируй текущую неделю на два месяца вперед
      - скопируй эту неделю на следующие 8 недель

    Must NOT catch:
      - скопируй эту тренировку на следующую неделю
      - скопируй выбранную тренировку
      - скопируй на следующую неделю
    """
    if not text:
        return False

    t = text.lower().replace("ё", "е").strip()

    has_copy = any(x in t for x in (
        "скопируй",
        "копируй",
        "перенеси",
        "продублируй",
        "дублируй",
    ))
    if not has_copy:
        return False

    if any(x in t for x in (
        "эту тренировку",
        "данную тренировку",
        "выбранную тренировку",
        "последнюю тренировку",
        "тренировку с ",
        "тренировку на ",
    )):
        return False

    has_week_source = any(x in t for x in (
        "тренировки этой недели",
        "тренировки текущей недели",
        "тренировки на этой неделе",
        "тренировки на текущей неделе",
        "тренировочный план этой недели",
        "тренировочный план текущей недели",
        "эту неделю",
        "текущую неделю",
        "эта неделя",
        "неделю целиком",
        "всю неделю",
    ))
    if not has_week_source:
        return False

    has_target = any(x in t for x in (
        "на следующую неделю",
        "на две недели",
        "на 2 недели",
        "на три недели",
        "на 3 недели",
        "на четыре недели",
        "на 4 недели",
        "на месяц",
        "на два месяца",
        "на 2 месяца",
        "на три месяца",
        "на 3 месяца",
        "на следующие",
        "вперед",
        "вперёд",
    ))

    return has_target


def _extract_week_copy_weeks_count(text: str | None) -> int:
    if not text:
        return 1

    t = text.lower().replace("ё", "е")

    if "два месяца" in t or "2 месяца" in t:
        return 8

    if "месяц" in t:
        return 4

    if "две недели" in t or "2 недели" in t:
        return 2

    if "три недели" in t or "3 недели" in t:
        return 3

    if "четыре недели" in t or "4 недели" in t:
        return 4

    m = re.search(r"следующ(?:ие|их)\s+(\d+)\s+нед", t)
    if m:
        return max(1, int(m.group(1)))

    m = re.search(r"на\s+(\d+)\s+нед", t)
    if m:
        return max(1, int(m.group(1)))

    if "на следующую неделю" in t:
        return 1

    return 1

# --- End week period copy priority helpers ---
def _is_show_active_workout_log_request(text: str | None) -> bool:
    """
    User wants the current in-progress workout log, not the planned workout template.

    Must catch:
      - покажи текущую тренировку
      - что я уже сделал
      - что уже сделал
      - покажи что я сделал
      - покажи записанные подходы
    """
    if not text:
        return False

    t = text.lower().replace("ё", "е").strip()

    return any(x in t for x in (
        "покажи текущую тренировку",
        "текущая тренировка",
        "что я уже сделал",
        "что уже сделал",
        "что сделал",
        "покажи что я сделал",
        "покажи записанные подходы",
        "записанные подходы",
        "что записано",
        "что я записал",
    ))

async def _handle_show_active_workout_log(telegram_user_id: str | None, text: str | None) -> str | None:
    """
    Show sets already logged in the active workout session.
    Falls back to None if there is no active workout log.
    """
    from app.db import AsyncSessionLocal
    from sqlalchemy import text as sql_text

    async with AsyncSessionLocal() as session:
        # Find latest active workout session for this Telegram user.
        result = await session.execute(
            sql_text(
                """
                SELECT id, planned_workout_id, workout_date, focus_label AS title
                  FROM fitness_workouts
                  WHERE telegram_user_id = :telegram_user_id
                    AND completion_type = 'active_session'
                  ORDER BY created_at DESC, id DESC
                  LIMIT 1
                """
            ),
            {"telegram_user_id": telegram_user_id},
        )
        workout = result.mappings().first()

        if not workout:
            return None

        workout_id = workout["id"]

        sets_result = await session.execute(
            sql_text(
                """
                SELECT
                    exercise_name,
                    set_number,
                    weight_kg,
                    reps
                FROM fitness_exercise_sets
                  WHERE workout_id = :workout_id
                ORDER BY id ASC
                """
            ),
            {"workout_id": workout_id},
        )
        rows = list(sets_result.mappings().all())

    title = workout.get("title") or "Текущая тренировка"
    workout_date = workout.get("workout_date")

    lines = [
        "Текущая тренировка:",
        "",
        f"{title}",
    ]

    if workout_date:
        lines.append(f"Дата: {workout_date}")

    if not rows:
        lines.extend([
            "",
            "Пока нет записанных подходов.",
        ])
        return "\n".join(lines)

    lines.extend([
        "",
        "Записанные подходы:",
    ])

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        name = row.get("exercise_name") or "Упражнение"
        grouped.setdefault(name, []).append(row)

    for exercise_name, sets in grouped.items():
        lines.append(f"- {exercise_name}:")
        for s in sets:
            set_number = s.get("set_number")
            weight = s.get("weight_kg")
            reps = s.get("reps")

            if weight is None:
                lines.append(f"  {set_number}) {reps} повторов")
            else:
                weight_str = ("%g" % float(weight)).replace(".", ",")
                lines.append(f"  {set_number}) {weight_str} кг × {reps}")

    return "\n".join(lines)


def _looks_like_delete_last_logged_set(text: str | None) -> bool:
    t = _clean(text).replace("ё", "е")
    return (
        ("удали" in t or "убери" in t or "сотри" in t)
        and ("последний" in t or "последнии" in t)
        and ("сет" in t or "подход" in t)
    )


def _extract_last_set_reps_edit(text: str | None) -> int | None:
    if not text:
        return None

    t = _clean(text).replace("ё", "е")

    if not (
        ("исправь" in t or "измени" in t or "поменяй" in t)
        and ("последний" in t or "последнии" in t)
        and ("подход" in t or "сет" in t)
    ):
        return None

    import re

    m = re.search(r"на\s+(\d+)\s*(?:повтор|раз|реп)", t)
    if m:
        return int(m.group(1))

    m = re.search(r"(\d+)\s*(?:повтор|раз|реп)", t)
    if m:
        return int(m.group(1))

    return None


async def _delete_last_logged_set_from_active_session(
    telegram_user_id: str | None,
    source_text: str | None = None,
) -> str | None:
    from app.db import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        active_result = await session.execute(
            text(
                """
                SELECT id
                FROM fitness_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND completion_type = 'active_session'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"telegram_user_id": str(telegram_user_id) if telegram_user_id else None},
        )
        active = active_result.mappings().first()

        if not active:
            return "Нет активной тренировки. Сначала напиши “начал тренировку”."

        set_result = await session.execute(
            text(
                """
                SELECT id, exercise_name, set_number, weight_kg, reps
                FROM fitness_exercise_sets
                WHERE workout_id = :workout_id
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {"workout_id": int(active["id"])},
        )
        row = set_result.mappings().first()

        if not row:
            return "В активной тренировке пока нет записанных подходов."

        await session.execute(
            text(
                """
                DELETE FROM fitness_exercise_sets
                WHERE id = :set_id
                """
            ),
            {"set_id": int(row["id"])},
        )
        await session.commit()

    name = row.get("exercise_name") or "упражнение"
    reps = row.get("reps")
    weight = row.get("weight_kg")

    if weight is None:
        deleted = f"{reps} повторов"
    else:
        weight_str = ("%g" % float(weight)).replace(".", ",")
        deleted = f"{weight_str} кг × {reps}"

    return f"Удалил последний подход.\n\n{name}: {deleted}"


async def _edit_last_logged_set_reps_in_active_session(
    telegram_user_id: str | None,
    reps: int,
    source_text: str | None = None,
) -> str | None:
    from app.db import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        active_result = await session.execute(
            text(
                """
                SELECT id
                FROM fitness_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND completion_type = 'active_session'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"telegram_user_id": str(telegram_user_id) if telegram_user_id else None},
        )
        active = active_result.mappings().first()

        if not active:
            return "Нет активной тренировки. Сначала напиши “начал тренировку”."

        set_result = await session.execute(
            text(
                """
                SELECT id, exercise_name, set_number, weight_kg, reps
                FROM fitness_exercise_sets
                WHERE workout_id = :workout_id
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {"workout_id": int(active["id"])},
        )
        row = set_result.mappings().first()

        if not row:
            return "В активной тренировке пока нет записанных подходов."

        await session.execute(
            text(
                """
                UPDATE fitness_exercise_sets
                SET reps = :reps
                WHERE id = :set_id
                """
            ),
            {
                "set_id": int(row["id"]),
                "reps": int(reps),
            },
        )
        await session.commit()

    name = row.get("exercise_name") or "упражнение"
    weight = row.get("weight_kg")

    if weight is None:
        updated = f"{reps} повторов"
    else:
        weight_str = ("%g" % float(weight)).replace(".", ",")
        updated = f"{weight_str} кг × {reps}"

    return f"Исправил последний подход.\n\n{name}: {updated}"


async def handle_router_hardening(telegram_user_id: str | None, text: str) -> str | None:
    # HARD PRIORITY: whole-week copy before generic selected-workout copy.
    if _is_this_week_period_copy_request(text):
        copy_week_priority_reply = await _handle_copy_this_week_workouts_period_priority(
            telegram_user_id,
            text,
        )
        if copy_week_priority_reply is not None:
            return copy_week_priority_reply

    from app.db import (
        create_fitness_pending_decision,
        get_latest_fitness_pending_decision,
        get_planned_workouts_in_period,
        get_today_planned_workout,
        get_next_planned_workouts,
        move_planned_workouts_between_dates,
        cleanup_empty_planned_workouts,
        get_recent_exercise_history,
    )

    pending = await get_latest_fitness_pending_decision(telegram_user_id)

    # Pending details must be handled before generic planned-workout parser.
    # Otherwise a reply like “сделаем жим присед...” is parsed as a new workout for today,
    # instead of filling the pending workout date selected in the previous message.
    reply = await _handle_custom_workout_details(telegram_user_id, text, pending)
    if reply is not None:
        return reply

    # Program import pending has priority over generic parser:
    # user answers with schedule like “пн ср пт сб на 4 недели” or “отмена”.
    reply = await handle_training_program_import_pending(telegram_user_id, text)
    if reply is not None:
        return reply

    # Pending details for adding multiple exercises to selected workout.
    reply = await _handle_add_exercises_to_selected_workout(telegram_user_id, text, pending)
    if reply is not None:
        return reply


    # Active workout log summary aliases must win over planned workout display.
    if _is_show_active_workout_log_request(text):
        active_summary = await _handle_show_active_workout_log(telegram_user_id, text)
        if active_summary is not None:
            return active_summary

    if _looks_like_delete_last_logged_set(text):
        delete_last_set_reply = await _delete_last_logged_set_from_active_session(
            telegram_user_id=telegram_user_id,
            source_text=text,
        )
        if delete_last_set_reply is not None:
            return delete_last_set_reply

    edit_last_reps = _extract_last_set_reps_edit(text)
    if edit_last_reps is not None:
        edit_last_set_reply = await _edit_last_logged_set_reps_in_active_session(
            telegram_user_id=telegram_user_id,
            reps=edit_last_reps,
            source_text=text,
        )
        if edit_last_set_reply is not None:
            return edit_last_set_reply

    # Dangerous/safety pending confirmations must have priority over parser.
    reply = await _handle_period_copy_confirmation(telegram_user_id, text, pending)
    if reply is not None:
        return reply

    reply = await _handle_cancel_planned_confirmation(telegram_user_id, text, pending)
    if reply is not None:
        return reply

    # Exercise disambiguation pending also has priority.
    reply = await _handle_exercise_disambiguation(telegram_user_id, text, pending)
    if reply is not None:
        return reply

    active_continuation_reply = await _log_continuation_set_to_active_session(
        telegram_user_id=telegram_user_id,
        text_value=text,
    )
    if active_continuation_reply is not None:
        return active_continuation_reply

    # If a workout session is active, exercise set messages must be logged
    # into the active factual workout, not converted into planned workouts.
    active_sets_reply = await _log_exercise_sets_to_active_session(
        telegram_user_id=telegram_user_id,
        text_value=text,
    )
    if active_sets_reply is not None:
        return active_sets_reply

    # Actual workout log bridge before planned-workout parser.
    if _looks_like_mark_selected_workout_done(text):
        return await _create_completed_workout_from_selected_plan(
            telegram_user_id=telegram_user_id,
            source_text=text,
        )

    if _looks_like_start_selected_workout(text):
        return await _start_selected_planned_workout_session(
            telegram_user_id=telegram_user_id,
            source_text=text,
        )

    if _looks_like_finish_active_workout(text):
        return await _finish_active_workout_session(
            telegram_user_id=telegram_user_id,
            source_text=text,
        )

    if _looks_like_show_last_actual_workout(text):
        from app.modules.fitness.handler import command_last_workout

        return await command_last_workout(telegram_user_id)

    # Copy month/custom period before week/single workout copy.
    copy_period_action = _parse_copy_month_or_custom_period_action(text)
    if copy_period_action:
        return await _build_period_copy_preview(
            telegram_user_id=telegram_user_id,
            action=copy_period_action,
            source_text=text,
        )

    # Copy whole week/period before generic selected-workout copy.
    copy_week_action = _parse_copy_week_period_action(text)
    if copy_week_action:
        # One-week copy is small and can remain immediate.
        # Multi-week copy is mass action and requires confirmation.
        if int(copy_week_action.get("target_weeks") or 1) > 1:
            return await _build_period_copy_preview(
                telegram_user_id=telegram_user_id,
                action=copy_week_action,
                source_text=text,
            )

        planned_reply = await execute_planned_workout_action(
            telegram_user_id=telegram_user_id,
            action=copy_week_action,
            source_text=text,
        )
        if planned_reply:
            return planned_reply

    # Delete currently selected workout only:
    # “удали эту тренировку”, “отмени эту тренировку”.
    if _looks_like_delete_selected_workout(text):
        from app.modules.fitness.planned_workout_executor import _get_selected_planned_workout_context

        selected_context = await _get_selected_planned_workout_context(telegram_user_id)
        target_date = selected_context.get("target_date") if selected_context else None

        if target_date:
            planned_action = {
                "action": "cancel_planned_workouts",
                "confidence": 0.99,
                "scope": "period",
                "start_date": target_date,
                "end_date": target_date,
                "affects": "planned_only",
                "requires_confirmation": True,
                "summary": "Отменить выбранную плановую тренировку",
            }
            planned_reply = await execute_planned_workout_action(
                telegram_user_id=telegram_user_id,
                action=planned_action,
                source_text=text,
            )
            if planned_reply:
                return planned_reply

    # Delete all workouts from a specific date onward:
    # “удали все тренировки с 18 мая”.
    delete_from_date = _parse_delete_from_date(text)
    if delete_from_date:
        planned_action = {
            "action": "cancel_planned_workouts",
            "confidence": 0.99,
            "scope": "period",
            "start_date": delete_from_date,
            "end_date": "2100-12-31",
            "affects": "planned_only",
            "requires_confirmation": True,
            "summary": "Отменить активные плановые тренировки с даты",
        }
        planned_reply = await execute_planned_workout_action(
            telegram_user_id=telegram_user_id,
            action=planned_action,
            source_text=text,
        )
        if planned_reply:
            return planned_reply

    # Training program import from long text.
    # Preview only: nothing is written to calendar until user provides schedule.
    if looks_like_training_program_text(text):
        return await preview_training_program_import(
            telegram_user_id=telegram_user_id,
            program_text=text,
            source_type="text",
            title="Импортированная программа тренировок",
        )

    # Parser-first layer for editing existing planned workouts.
    # This must run before generic planning parser, otherwise phrases like
    # "добавь восьмым упражнением велосипед" may be misread as creating a new workout.
    edit_action = await parse_workout_edit_action(
        text=text,
        context={
            "has_pending": bool(pending),
            "pending_type": pending.get("decision_type") if pending else None,
        },
    )
    edit_reply = await execute_planned_workout_action(
        telegram_user_id=telegram_user_id,
        action=edit_action,
        source_text=text,
    )
    if edit_reply is not None:
        return edit_reply

    # Selected-workout copy commands may omit the word "тренировка":
    # "скопируй на следующую неделю", "продублируй на весь месяц".
    # Route them to the fitness planned-workout parser before generic task logging.
    if any(x in _clean(text) for x in ["скоп", "копир", "дублир", "продублир", "повтори"]):
        planned_action = await parse_planned_workout_action(telegram_user_id, text)
        if planned_action:
            planned_reply = await execute_planned_workout_action(
                telegram_user_id=telegram_user_id,
                action=planned_action,
                source_text=text,
            )
            if planned_reply:
                return planned_reply

    # Parser-first layer for planned workout operations.
    # Hard commands are only shortcuts; free speech goes through parser -> structured action -> executor.
    planned_action = await parse_planned_workout_action(
        text=text,
        context={
            "has_pending": bool(pending),
            "pending_type": pending.get("decision_type") if pending else None,
        },
    )
    planned_reply = await execute_planned_workout_action(
        telegram_user_id=telegram_user_id,
        action=planned_action,
        source_text=text,
    )
    if planned_reply is not None:
        return planned_reply

    # 3. Запрет пустой custom workout: create pending and ask details.
    if _is_empty_custom_workout_request(text):
        target_date = _parse_ru_date(text) or _iso(_today())

        await create_fitness_pending_decision(
            telegram_user_id=telegram_user_id,
            decision_type="awaiting_custom_workout_details",
            context={
                "target_date": target_date,
                "source_text": text,
            },
            source_text=text,
        )

        return f"Ок, добавим тренировку на {target_date}. Какие упражнения будем делать?"

    # 4. Следующая тренировка / следующая тренировка с весами.
    if _is_next_workout_query(text):
        items = await get_next_planned_workouts(telegram_user_id=telegram_user_id, limit=3)
        if not items:
            return "Следующая активная тренировка не найдена."

        if _wants_weights(text):
            return await _format_workouts_with_weights(
                telegram_user_id=telegram_user_id,
                items=items[:1],
                title="Следующая тренировка с весами:",
            )

        if len(items) > 1 and items[0]["workout"]["planned_date"] == items[1]["workout"]["planned_date"]:
            return format_period_plan(items, title="На ближайшую дату найдено несколько тренировок:")

        return "Следующая тренировка:\n\n" + format_planned_workout(items[0])

    # 5. Тренировка на дату / с весами.
    if _is_workout_on_date_query(text):
        target_date = _parse_ru_date(text)
        if target_date:
            data = await get_today_planned_workout(telegram_user_id, target_date)
            human_date = format_human_date(target_date)
            if not data:
                return f"На {human_date} активная плановая тренировка не найдена."

            if _wants_weights(text):
                return await _format_workouts_with_weights(
                    telegram_user_id=telegram_user_id,
                    items=[data],
                    title=f"Тренировка на {human_date} с весами:",
                )

            return f"Тренировка на {human_date}:\n\n" + format_planned_workout(data)

    # 6. Move/copy workout между датами.
    t = _clean(text)
    if "перенеси" in t and "трениров" in t:
        source_date, target_date = _parse_two_dates_for_move(text)
        if source_date and target_date:
            moved = await move_planned_workouts_between_dates(
                telegram_user_id=telegram_user_id,
                source_date=source_date,
                target_date=target_date,
                source_text=text,
                mode="move",
            )
            return (
                f"Перенёс плановые тренировки с {source_date} на {target_date}.\n"
                f"Перенесено: {moved}"
            )

    if any(x in t for x in ["хочу сделать", "сделать тренировку", "возьми тренировку"]):
        source_date, target_date = _parse_two_dates_for_move(text)
        if source_date and target_date:
            moved = await move_planned_workouts_between_dates(
                telegram_user_id=telegram_user_id,
                source_date=source_date,
                target_date=target_date,
                source_text=text,
                mode="copy",
            )
            return (
                f"Скопировал плановые тренировки с {source_date} на {target_date}.\n"
                f"Создано копий: {moved}"
            )

    # 7. Отмена будущего/недельного планового периода через preview.
    if _is_delete_planned_period_request(text):
        start_date = None
        end_date = None

        if "следующ" in t and "недел" in t:
            start_date, end_date = next_week_bounds()
        elif "текущ" in t and "недел" in t:
            start_date, end_date = week_bounds()
        elif "от сегодня" in t:
            start_date = _iso(_today())
            _, month_end = month_bounds()
            end_date = month_end
        elif "недел" in t:
            start_date, end_date = week_bounds()

        if not start_date or not end_date:
            return "Понял, что нужно отменить плановые тренировки, но не понял период."

        items = await get_planned_workouts_in_period(
            telegram_user_id=telegram_user_id,
            start_date=start_date,
            end_date=end_date,
            include_cancelled=False,
        )

        if not items:
            return f"За период {start_date} — {end_date} активных плановых тренировок не найдено."

        await create_fitness_pending_decision(
            telegram_user_id=telegram_user_id,
            decision_type="confirm_cancel_planned_period",
            context={
                "start_date": start_date,
                "end_date": end_date,
                "source_text": text,
            },
            source_text=text,
        )

        preview = format_period_plan(items, title=f"Будут отменены плановые тренировки {start_date} — {end_date}:")
        return (
            preview
            + "\n\nФактическую историю тренировок не трогаю.\n"
            + "Подтверди обычным текстом: “да, отмени эти плановые тренировки” или “отмена”."
        )

    # 8. Улучшенная история упражнения до старого history-parser.
    if _is_history_request(text):
        exercise_name = _extract_exercise_from_history_text(text)

        if exercise_name:
            # Generic words must ask clarification.
            if _clean(exercise_name) in GENERIC_EXERCISE_WORDS:
                matches = possible_matches(exercise_name)
                lines = ["Уточни, какое упражнение имеешь в виду:"]
                for m in matches:
                    lines.append(f"- {m['canonical_ru']}")
                await create_fitness_pending_decision(
                    telegram_user_id=telegram_user_id,
                    decision_type="awaiting_exercise_disambiguation",
                    context={"original_text": text, "limit": 3},
                    source_text=text,
                )
                return "\n".join(lines)

            normalized = normalize_exercise_name(exercise_name)
            key = normalized.get("exercise_key")

            if key:
                limit = 2 if "позапрош" in _clean(text) else 3
                history = await get_recent_exercise_history(
                    telegram_user_id=telegram_user_id,
                    exercise_key=key,
                    limit_workouts=limit,
                )
                from app.modules.fitness.exercise_history import format_exercise_history
                return format_exercise_history(history, get_exercise_title(key, exercise_name), limit=limit)

            matches = possible_matches(exercise_name)
            if matches:
                await create_fitness_pending_decision(
                    telegram_user_id=telegram_user_id,
                    decision_type="awaiting_exercise_disambiguation",
                    context={"original_text": text, "limit": 3},
                    source_text=text,
                )
                lines = ["Уточни, какое упражнение имеешь в виду:"]
                for m in matches:
                    lines.append(f"- {m['canonical_ru']}")
                return "\n".join(lines)

    # 9. Cleanup command.
    if "cleanup" in t or "очисти пустые" in t or "удали пустые" in t:
        count = await cleanup_empty_planned_workouts(telegram_user_id=telegram_user_id)
        return f"Очистил пустые плановые тренировки. Отменено: {count}"

    return None
