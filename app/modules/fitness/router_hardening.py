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
from app.modules.fitness.formatter import format_planned_workout, format_period_plan
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

    if not any(x in t for x in ["эту неделю", "текущую неделю", "неделю"]):
        return None

    # Do not hijack selected single workout copy.
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


async def handle_router_hardening(telegram_user_id: str | None, text: str) -> str | None:
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

    # Dangerous/safety pending confirmations must have priority over parser.
    reply = await _handle_cancel_planned_confirmation(telegram_user_id, text, pending)
    if reply is not None:
        return reply

    # Exercise disambiguation pending also has priority.
    reply = await _handle_exercise_disambiguation(telegram_user_id, text, pending)
    if reply is not None:
        return reply

    # Copy whole week/period before generic selected-workout copy.
    copy_week_action = _parse_copy_week_period_action(text)
    if copy_week_action:
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
            if not data:
                return f"На {target_date} активная плановая тренировка не найдена."

            if _wants_weights(text):
                return await _format_workouts_with_weights(
                    telegram_user_id=telegram_user_id,
                    items=[data],
                    title=f"Тренировка на {target_date} с весами:",
                )

            return f"Тренировка на {target_date}:\n\n" + format_planned_workout(data)

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
