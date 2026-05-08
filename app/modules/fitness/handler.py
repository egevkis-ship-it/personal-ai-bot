from datetime import date

from app.db import (
    save_training_plan,
    get_today_planned_workout,
    get_next_planned_workout,
    get_week_plan,
    save_fitness_workout,
    save_body_measurement,
    get_last_workout,
    get_last_measurement,
)
from app.modules.fitness.parser import parse_fitness_action
from app.modules.fitness.formatter import (
    format_planned_workout,
    format_week_plan,
    format_completed_workout,
    format_measurement,
    format_last_workout,
    format_last_measurement,
)
from app.modules.fitness.utils import week_bounds


async def handle_fitness_text(telegram_user_id: str | None, text: str) -> str:
    parsed = parse_fitness_action(text)
    action = parsed.get("action")

    if action == "create_plan":
        plan = parsed.get("plan") or {}
        plan_id = await save_training_plan(
            telegram_user_id=telegram_user_id,
            plan_name=plan.get("plan_name"),
            period_type=plan.get("period_type") or (parsed.get("period") or {}).get("period_type"),
            start_date=plan.get("start_date") or (parsed.get("period") or {}).get("start_date"),
            end_date=plan.get("end_date") or (parsed.get("period") or {}).get("end_date"),
            source_text=text,
            notes=plan.get("notes"),
            planned_workouts=plan.get("planned_workouts") or [],
        )

        count = len(plan.get("planned_workouts") or [])
        return f"Создал тренировочный план.\nID плана: {plan_id}\nТренировок: {count}\n\nНапиши /week_plan, чтобы посмотреть неделю."

    if action == "get_today_workout":
        today = date.today().isoformat()
        data = await get_today_planned_workout(telegram_user_id, today)
        if data:
            return "Сегодня по плану:\n\n" + format_planned_workout(data)

        next_data = await get_next_planned_workout(telegram_user_id)
        if next_data:
            return "На сегодня тренировка не найдена.\n\nСледующая невыполненная:\n\n" + format_planned_workout(next_data)

        return "На сегодня тренировка не найдена, и активного плана тоже не вижу."

    if action == "get_next_workout":
        data = await get_next_planned_workout(telegram_user_id)
        return "Следующая тренировка:\n\n" + format_planned_workout(data) if data else "Следующая тренировка не найдена."

    if action == "get_week_plan":
        start, end = week_bounds()
        items = await get_week_plan(telegram_user_id, start, end)
        return format_week_plan(items)

    if action == "get_focus_workout":
        # Пока используем простую стратегию: показываем следующую тренировку.
        # Поиск по focus добавим в следующем пакете вместе с редактированием плана.
        data = await get_next_planned_workout(telegram_user_id)
        if not data:
            return "Активная тренировка по этому фокусу пока не найдена."
        return "Пока поиск по фокусу работает упрощённо. Ближайшая подходящая/следующая тренировка:\n\n" + format_planned_workout(data)

    if action == "log_workout":
        completed = parsed.get("completed_workout") or {}

        linked = await _find_obvious_planned_workout(telegram_user_id, completed)

        planned_workout_id = None
        linked_title = None
        completion_type = completed.get("completion_type") or "custom"

        if linked:
            planned_workout_id = linked["workout"]["id"]
            linked_title = linked["workout"].get("title") or linked["workout"].get("focus_label")
            if completion_type == "custom":
                completion_type = "as_planned"

        workout_id = await save_fitness_workout(
            telegram_user_id=telegram_user_id,
            workout_date=completed.get("workout_date") or parsed.get("date") or date.today().isoformat(),
            workout_type=completed.get("workout_type"),
            focus=completed.get("focus"),
            focus_label=completed.get("focus_label"),
            bodyweight_kg=completed.get("bodyweight_kg"),
            source_text=text,
            notes=completed.get("notes"),
            exercises=completed.get("exercises") or [],
            planned_workout_id=planned_workout_id,
            completion_type=completion_type,
        )

        # Если в тексте был вес тела — также пишем замер.
        measurement_id = None
        if completed.get("bodyweight_kg"):
            measurement_id = await save_body_measurement(
                telegram_user_id=telegram_user_id,
                measurement_date=completed.get("workout_date") or parsed.get("date") or date.today().isoformat(),
                source_text=text,
                data={"weight_kg": completed.get("bodyweight_kg")},
            )

        reply = format_completed_workout(parsed, workout_id=workout_id, linked_plan_title=linked_title)
        if measurement_id:
            reply += f"\n\nТакже записал вес как замер. ID замера: {measurement_id}"
        return reply

    if action == "log_measurement":
        m = parsed.get("body_measurements") or {}
        measurement_id = await save_body_measurement(
            telegram_user_id=telegram_user_id,
            measurement_date=m.get("measurement_date") or parsed.get("date") or date.today().isoformat(),
            source_text=text,
            data=m,
        )
        return format_measurement(parsed, measurement_id=measurement_id)

    if action == "skip_workout":
        return "Я понял, что нужно отметить пропуск. В следующем пакете добавлю изменение статуса плана: skipped."

    if action == "change_plan":
        return "Я понял, что нужно изменить план. В следующем пакете добавлю переносы, замены и перестановки."

    return "Я понял сообщение как фитнес-контекст, но пока не смог уверенно выбрать действие. Попробуй сформулировать: “создай план”, “дай сегодняшнюю тренировку” или “сделал тренировку”."


async def _find_obvious_planned_workout(telegram_user_id: str | None, completed: dict) -> dict | None:
    today = completed.get("workout_date") or date.today().isoformat()
    today_plan = await get_today_planned_workout(telegram_user_id, today)

    if today_plan:
        planned_focus = today_plan["workout"].get("focus")
        completed_focus = completed.get("focus")

        if not completed_focus or not planned_focus or planned_focus == completed_focus:
            return today_plan

    return None


async def command_today_workout(telegram_user_id: str | None) -> str:
    data = await get_today_planned_workout(telegram_user_id, date.today().isoformat())
    if data:
        return "Сегодня по плану:\n\n" + format_planned_workout(data)

    next_data = await get_next_planned_workout(telegram_user_id)
    if next_data:
        return "На сегодня тренировка не найдена.\n\nСледующая невыполненная:\n\n" + format_planned_workout(next_data)

    return "На сегодня тренировка не найдена."


async def command_next_workout(telegram_user_id: str | None) -> str:
    data = await get_next_planned_workout(telegram_user_id)
    return "Следующая тренировка:\n\n" + format_planned_workout(data) if data else "Следующая тренировка не найдена."


async def command_week_plan(telegram_user_id: str | None) -> str:
    start, end = week_bounds()
    items = await get_week_plan(telegram_user_id, start, end)
    return format_week_plan(items)


async def command_last_workout(telegram_user_id: str | None) -> str:
    return format_last_workout(await get_last_workout(telegram_user_id))


async def command_last_measurement(telegram_user_id: str | None) -> str:
    return format_last_measurement(await get_last_measurement(telegram_user_id))
