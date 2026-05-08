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
    get_planned_workout_by_focus,
    get_planned_workout_by_id,
    mark_planned_workout_skipped,
    move_planned_workout,
    swap_planned_workouts,
    replace_planned_workout,
)
from app.modules.fitness.parser import parse_fitness_action
from app.modules.fitness.change_parser import parse_plan_change
from app.modules.fitness.formatter import (
    format_planned_workout,
    format_week_plan,
    format_completed_workout,
    format_measurement,
    format_last_workout,
    format_last_measurement,
    format_human_date,
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
        query = parsed.get("query") or {}
        data = await get_planned_workout_by_focus(
            telegram_user_id,
            query.get("focus"),
            query.get("focus_label"),
        )
        if data:
            return "Тренировка по запросу:\n\n" + format_planned_workout(data)
        return "Активная тренировка по этому фокусу пока не найдена."

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

    if action in ("skip_workout", "change_plan"):
        return await handle_plan_change(telegram_user_id, text)

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


async def _find_target_workout(telegram_user_id: str | None, target: dict) -> dict | None:
    if target.get("is_today"):
        return await get_today_planned_workout(telegram_user_id, date.today().isoformat())

    if target.get("is_next"):
        return await get_next_planned_workout(telegram_user_id)

    if target.get("focus") or target.get("focus_label"):
        return await get_planned_workout_by_focus(
            telegram_user_id,
            target.get("focus"),
            target.get("focus_label"),
        )

    if target.get("date"):
        return await get_today_planned_workout(telegram_user_id, target.get("date"))

    return await get_next_planned_workout(telegram_user_id)


async def handle_plan_change(telegram_user_id: str | None, text: str) -> str:
    change = parse_plan_change(text)
    change_type = change.get("change_type")
    target = change.get("target") or {}

    if change_type == "skip":
        data = await _find_target_workout(telegram_user_id, target)
        if not data:
            return "Не нашёл тренировку, которую нужно пропустить."

        workout = data["workout"]
        await mark_planned_workout_skipped(
            planned_workout_id=workout["id"],
            source_text=text,
            reason=change.get("reason"),
        )

        updated = await get_planned_workout_by_id(workout["id"])

        return (
            "Отметил тренировку как пропущенную.\n\n"
            + format_planned_workout(updated)
        )

    if change_type == "move":
        data = await _find_target_workout(telegram_user_id, target)
        if not data:
            return "Не нашёл тренировку, которую нужно перенести."

        new_date = change.get("new_date")
        if not new_date:
            return "Понял перенос, но не понял новую дату. Напиши, например: “перенеси плечи на пятницу”."

        workout = data["workout"]
        await move_planned_workout(
            planned_workout_id=workout["id"],
            new_date=new_date,
            new_weekday=change.get("new_weekday"),
            source_text=text,
        )

        updated = await get_planned_workout_by_id(workout["id"])
        title = workout.get("title") or workout.get("focus_label") or "тренировка"

        return (
            f"Перенёс тренировку: {title} → {format_human_date(new_date)}.\n\n"
            + format_planned_workout(updated)
        )

    if change_type == "swap":
        first = await _find_target_workout(telegram_user_id, target)
        second_target = change.get("second_target") or {}
        second = await _find_target_workout(telegram_user_id, second_target)

        if not first or not second:
            return "Не нашёл обе тренировки для обмена местами."

        await swap_planned_workouts(
            first_workout_id=first["workout"]["id"],
            second_workout_id=second["workout"]["id"],
            source_text=text,
        )

        first_updated = await get_planned_workout_by_id(first["workout"]["id"])
        second_updated = await get_planned_workout_by_id(second["workout"]["id"])

        return (
            "Поменял тренировки местами.\n\n"
            "Теперь:\n\n"
            + format_planned_workout(first_updated)
            + "\n\n"
            + format_planned_workout(second_updated)
        )

    if change_type == "replace":
        data = await _find_target_workout(telegram_user_id, target)
        if not data:
            return "Не нашёл тренировку, которую нужно заменить."

        replacement = change.get("replacement") or {}
        if not replacement.get("focus") and not replacement.get("focus_label") and not replacement.get("title"):
            return "Понял замену, но не понял, на какую тренировку заменить."

        replacement_id = await replace_planned_workout(
            target_workout_id=data["workout"]["id"],
            replacement=replacement,
            source_text=text,
        )

        updated_replacement = await get_planned_workout_by_id(replacement_id)

        return (
            "Заменил тренировку.\n\n"
            f"Было: {data['workout'].get('title') or data['workout'].get('focus_label')}\n\n"
            "Стало:\n"
            + format_planned_workout(updated_replacement)
        )

    if change_type == "custom_today":
        return "Понял кастомную тренировку на сегодня. В следующем пакете добавлю создание кастомной плановой тренировки."

    return "Я понял, что ты хочешь изменить план, но пока не смог уверенно разобрать действие."
