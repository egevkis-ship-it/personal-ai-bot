from __future__ import annotations

import re
from datetime import date, timedelta

from app.modules.fitness.exercise_history import handle_exercise_history_request
from app.modules.fitness.custom_workout_builder import create_custom_workout_from_details
from app.modules.fitness.planned_workout_parser import parse_planned_workout_action
from app.modules.fitness.planned_workout_executor import execute_planned_workout_action
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
    if not any(x in t for x in ["добав", "создай", "поставь"]):
        return False
    if "трениров" not in t:
        return False
    # If after colon user gave exercises, it is not empty.
    if ":" in t:
        return False
    # If obvious exercise words are present, it is not empty.
    if any(x in t for x in ["жим", "тяга", "махи", "присед", "развод", "сгиб", "разгиб", "подтяг"]):
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

    t = _clean(text)

    # Confirmation must be explicit.
    # Do NOT treat generic words like "давай" as confirmation:
    # "давай добавим тренировку" is a new command, not approval to delete plans.
    confirm_phrases = [
        "да отмени",
        "да, отмени",
        "да отменяй",
        "да, отменяй",
        "да удали",
        "да, удали",
        "да удаляй",
        "да, удаляй",
        "да очисти",
        "да, очисти",
        "подтверждаю",
        "подтверждаю отмену",
        "подтверждаю удаление",
        "отменяй",
        "удаляй",
    ]

    cancel_words = [
        "нет",
        "отмена",
        "не надо",
        "оставь",
        "не трогай",
        "стоп",
    ]

    new_command_markers = [
        "добав",
        "создай",
        "поставь",
        "покажи",
        "дай",
        "какая",
        "какой",
        "что у меня",
        "запиши",
        "перенеси",
        "хочу",
    ]

    if any(x in t for x in cancel_words):
        await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return "Ок, план не трогаю."

    if any(x in t for x in new_command_markers):
        return (
            "Сейчас ожидается подтверждение отмены плановых тренировок. "
            "Чтобы не удалить план случайно, сначала ответь: “да, отмени” или “отмена”."
        )

    if not any(x in t for x in confirm_phrases):
        return (
            "Я жду явное подтверждение отмены плановых тренировок. "
            "Напиши: “да, отмени” или “отмена”."
        )

    context = pending.get("context_json") or {}
    start_date = context.get("start_date")
    end_date = context.get("end_date")

    if not start_date or not end_date:
        return "Не нашёл период для отмены. План не трогаю."

    cancelled_count = await cancel_active_planned_workouts_in_period(
        telegram_user_id=telegram_user_id,
        start_date=start_date,
        end_date=end_date,
        source_text=text,
    )

    await resolve_fitness_pending_decision(pending["id"], status="resolved")

    return (
        f"Отменил плановые тренировки за период {start_date} — {end_date}.\n"
        f"Отменено тренировок: {cancelled_count}\n"
        "Фактическую историю тренировок не трогал."
    )




async def _handle_custom_workout_details(telegram_user_id: str | None, text: str, pending: dict) -> str | None:
    if not pending or pending.get("decision_type") != "awaiting_custom_workout_details":
        return None

    from app.db import resolve_fitness_pending_decision

    context = pending.get("context_json") or {}
    target_date = context.get("target_date") or _iso(_today())

    t = _clean(text)

    if t in {"отмена", "отмени", "не надо", "не добавляй", "cancel"}:
        await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return "Ок, тренировку не создаю."

    reply = await create_custom_workout_from_details(
        telegram_user_id=telegram_user_id,
        text=text,
        target_date=target_date,
    )

    await resolve_fitness_pending_decision(pending["id"], status="resolved")
    return reply

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

    # 0. Pending details for custom workout creation.
    reply = await _handle_custom_workout_details(telegram_user_id, text, pending)
    if reply is not None:
        return reply

    # 1. Pending уточнение упражнения должно иметь приоритет.
    reply = await _handle_exercise_disambiguation(telegram_user_id, text, pending)
    if reply is not None:
        return reply

    # 2. Pending отмена планового периода.
    reply = await _handle_cancel_planned_confirmation(telegram_user_id, text, pending)
    if reply is not None:
        return reply

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
