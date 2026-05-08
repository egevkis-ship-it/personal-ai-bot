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
    get_latest_planned_workout_template_by_focus,
    get_planned_workouts_on_date,
    get_fitness_debug_week,
    create_fitness_pending_decision,
    get_latest_fitness_pending_decision,
    resolve_fitness_pending_decision,
    find_nearest_free_training_date,
)
from app.modules.fitness.parser import parse_fitness_action
from app.modules.fitness.change_parser import parse_plan_change
from app.modules.fitness.pending_parser import parse_pending_decision_response
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
    pending_reply = await maybe_handle_pending_decision(telegram_user_id, text)
    if pending_reply is not None:
        return pending_reply

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
        conflicts = await _build_same_day_warning(telegram_user_id, new_date)

        return (
            f"Перенёс тренировку: {title} → {format_human_date(new_date)}.\n\n"
            + format_planned_workout(updated)
            + conflicts
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

        first_date = first_updated["workout"].get("planned_date") if first_updated else None
        second_date = second_updated["workout"].get("planned_date") if second_updated else None

        warnings = ""
        if first_date:
            warnings += await _build_same_day_warning(telegram_user_id, first_date)
        if second_date and second_date != first_date:
            warnings += await _build_same_day_warning(telegram_user_id, second_date)

        return (
            "Поменял тренировки местами.\n\n"
            "Теперь:\n\n"
            + format_planned_workout(first_updated)
            + "\n\n"
            + format_planned_workout(second_updated)
            + warnings
        )

    if change_type == "replace":
        data = await _find_target_workout(telegram_user_id, target)
        if not data:
            return "Не нашёл тренировку, которую нужно заменить."

        replacement = change.get("replacement") or {}
        if not replacement.get("focus") and not replacement.get("focus_label") and not replacement.get("title"):
            return "Понял замену, но не понял, на какую тренировку заменить."

        replacement = await _enrich_replacement_from_template(
            telegram_user_id=telegram_user_id,
            target_workout_id=data["workout"]["id"],
            replacement=replacement,
        )

        replacement_id = await replace_planned_workout(
            target_workout_id=data["workout"]["id"],
            replacement=replacement,
            source_text=text,
        )

        updated_replacement = await get_planned_workout_by_id(replacement_id)
        replacement_date = updated_replacement["workout"].get("planned_date") if updated_replacement else None
        conflicts = await _build_same_day_warning(telegram_user_id, replacement_date)

        return (
            "Заменил тренировку.\n\n"
            f"Было: {data['workout'].get('title') or data['workout'].get('focus_label')}\n\n"
            "Стало:\n"
            + format_planned_workout(updated_replacement)
            + conflicts
        )

    if change_type == "custom_today":
        return "Понял кастомную тренировку на сегодня. В следующем пакете добавлю создание кастомной плановой тренировки."

    return "Я понял, что ты хочешь изменить план, но пока не смог уверенно разобрать действие."



async def maybe_handle_pending_decision(telegram_user_id: str | None, text: str) -> str | None:
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if not pending:
        return None

    decision_type = pending.get("decision_type")
    context = pending.get("context_json") or {}

    if decision_type != "same_day_conflict":
        return None

    parsed = parse_pending_decision_response(text, context)

    if not parsed.get("relates_to_pending"):
        return None

    action = parsed.get("action")

    if action == "keep_both":
        await resolve_fitness_pending_decision(pending["id"], status="resolved")
        return "Оставил обе тренировки в этот день. Конфликт закрыл."

    if action == "cancel_pending":
        await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return "Окей, ничего дополнительно не меняю. Конфликт закрыл."

    if action == "show_week_plan":
        return await command_week_plan(telegram_user_id)

    if action in ("move_workout", "move_to_nearest_free_day"):
        target = parsed.get("target") or {}
        target_id = target.get("planned_workout_id")

        target_workout = _find_workout_in_pending_context(context, target)

        if target_workout:
            target_id = target_workout.get("planned_workout_id")

        if not target_id:
            return (
                "Я понял, что нужно перенести тренировку, но не понял какую именно. "
                "Напиши, например: “перенеси грудь на воскресенье”."
            )

        if action == "move_to_nearest_free_day":
            new_date = await find_nearest_free_training_date(
                telegram_user_id=telegram_user_id,
                start_date=context.get("planned_date"),
            )
            if not new_date:
                return "Не нашёл свободный день в ближайшие 14 дней."
        else:
            new_date = parsed.get("new_date")

        if not new_date:
            return "Я понял перенос, но не понял новую дату."

        workout_before = await get_planned_workout_by_id(int(target_id))
        await move_planned_workout(
            planned_workout_id=int(target_id),
            new_date=new_date,
            new_weekday=parsed.get("new_weekday"),
            source_text=text,
        )

        await resolve_fitness_pending_decision(pending["id"], status="resolved")

        workout_after = await get_planned_workout_by_id(int(target_id))
        title = None
        if workout_before:
            title = workout_before["workout"].get("title") or workout_before["workout"].get("focus_label")
        title = title or "тренировка"

        return (
            f"Готово. Перенёс {title} на {format_human_date(new_date)}.\\n\\n"
            + format_planned_workout(workout_after)
        )

    return (
        "Я понял, что это ответ на конфликт, но не смог уверенно разобрать действие. "
        "Напиши, например: “оставь обе” или “перенеси грудь на воскресенье”."
    )


def _find_workout_in_pending_context(context: dict, target: dict) -> dict | None:
    workouts = context.get("workouts") or []

    target_id = target.get("planned_workout_id")
    if target_id:
        for w in workouts:
            if str(w.get("planned_workout_id")) == str(target_id):
                return w

    focus = target.get("focus")
    focus_label = (target.get("focus_label") or "").lower()

    for w in workouts:
        if focus and w.get("focus") == focus:
            return w
        if focus_label and (w.get("focus_label") or "").lower() == focus_label:
            return w

    return None


async def _enrich_replacement_from_template(
    telegram_user_id: str | None,
    target_workout_id: int,
    replacement: dict,
) -> dict:
    exercises = replacement.get("exercises") or []
    real_exercises = [e for e in exercises if e.get("exercise_name")]

    if real_exercises:
        replacement["exercises"] = real_exercises
        return replacement

    template = await get_latest_planned_workout_template_by_focus(
        telegram_user_id=telegram_user_id,
        focus=replacement.get("focus"),
        exclude_workout_id=target_workout_id,
    )

    if not template:
        replacement["exercises"] = []
        replacement["notes"] = (replacement.get("notes") or "") + "\nУпражнения не найдены: нужен ручной ввод."
        return replacement

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
    if not replacement.get("title"):
        replacement["title"] = replacement.get("focus_label") or template["workout"].get("title")
    replacement["notes"] = (replacement.get("notes") or "") + f"\nУпражнения скопированы из: {template['workout'].get('title') or template['workout'].get('focus_label')}."

    return replacement


async def _build_same_day_warning(telegram_user_id: str | None, planned_date) -> str:
    if not planned_date:
        return ""

    planned_date_str = str(planned_date)
    items = await get_planned_workouts_on_date(telegram_user_id, planned_date_str)
    if len(items) <= 1:
        return ""

    context_items = []
    lines = [
        "",
        "",
        f"Внимание: на {format_human_date(planned_date_str)} теперь несколько тренировок:"
    ]

    for item in items:
        w = item["workout"]
        title = w.get("title") or w.get("focus_label") or "тренировка"
        lines.append(f"- {title}")
        context_items.append({
            "planned_workout_id": w.get("id"),
            "title": title,
            "focus": w.get("focus"),
            "focus_label": w.get("focus_label"),
            "planned_date": str(w.get("planned_date")) if w.get("planned_date") else None,
        })

    context = {
        "decision_type": "same_day_conflict",
        "planned_date": planned_date_str,
        "workouts": context_items,
    }

    await create_fitness_pending_decision(
        telegram_user_id=telegram_user_id,
        decision_type="same_day_conflict",
        context=context,
        source_text="auto_detected_same_day_conflict",
    )

    lines.extend([
        "",
        "Я пока ничего дополнительно не менял.",
        "",
        "Можешь написать обычным текстом, например:",
        "- оставь обе в этот день",
        "- перенеси грудь на воскресенье",
        "- перенеси плечи на понедельник",
        "- перенеси грудь на ближайший свободный день",
        "- покажи план недели",
    ])

    return "\n".join(lines)


async def command_fitness_debug_week(telegram_user_id: str | None) -> str:
    start, end = week_bounds()
    rows = await get_fitness_debug_week(telegram_user_id, start, end)

    if not rows:
        return "Debug: на эту неделю записей нет."

    lines = ["Fitness debug week:"]
    for r in rows:
        lines.append(
            f"ID {r.get('id')} | plan {r.get('plan_id')} | {r.get('planned_date')} | "
            f"seq {r.get('sequence_number')} | {r.get('focus_label')} | "
            f"{r.get('status')} | replaced_by={r.get('replaced_by_id')}"
        )

    return "\n".join(lines)
