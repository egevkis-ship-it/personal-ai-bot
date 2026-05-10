from __future__ import annotations

from datetime import date, timedelta

from app.modules.fitness.custom_workout_builder import create_custom_workout_from_details
from app.modules.fitness.formatter import format_planned_workout, format_period_plan
from app.modules.fitness.exercise_normalizer import normalize_exercise_name


def _today_iso() -> str:
    return date.today().isoformat()


def _month_end_iso() -> str:
    today = date.today()
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    return (next_month - timedelta(days=1)).isoformat()


def _date_title(target_date: str) -> str:
    return f"Тренировка на {target_date}"


def _is_non_empty_workout(item: dict) -> bool:
    return bool((item or {}).get("exercises"))


def _format_workouts_on_date(items: list[dict], target_date: str, include_weights: bool = False) -> str:
    active_items = [
        item for item in (items or [])
        if (item.get("workout") or {}).get("status") == "planned"
    ]

    if not active_items:
        return f"На {target_date} активных плановых тренировок не найдено."

    non_empty = [item for item in active_items if _is_non_empty_workout(item)]
    empty = [item for item in active_items if not _is_non_empty_workout(item)]

    ordered = non_empty + empty

    if len(ordered) == 1:
        title = f"{_date_title(target_date)}:"
        if include_weights:
            title = f"{_date_title(target_date)} с весами:"
        return title + "\n\n" + format_planned_workout(ordered[0])

    lines = [f"На {target_date} найдено активных тренировок: {len(ordered)}"]

    for i, item in enumerate(ordered, start=1):
        lines.append("")
        lines.append(f"#{i}")
        lines.append(format_planned_workout(item))

    if empty:
        lines.append("")
        lines.append(f"Пустые плановые тренировки показаны внизу: {len(empty)}")

    return "\n".join(lines)


def _format_next_workouts(items: list[dict], include_weights: bool = False) -> str:
    if not items:
        return "Следующая активная тренировка не найдена."

    first_date = (items[0].get("workout") or {}).get("planned_date")
    same_day_items = [
        item for item in items
        if (item.get("workout") or {}).get("planned_date") == first_date
    ]

    if len(same_day_items) == 1:
        title = "Следующая тренировка:"
        if include_weights:
            title = "Следующая тренировка с весами:"
        return title + "\n\n" + format_planned_workout(same_day_items[0])

    title = "На ближайшую дату найдено несколько тренировок"
    if include_weights:
        title = "На ближайшую дату найдено несколько тренировок с весами"

    return format_period_plan(same_day_items, title=title)


async def _format_workouts_with_weights(
    telegram_user_id: str | None,
    items: list[dict],
    title: str,
) -> str:
    from app.db import get_recent_exercise_history

    if not items:
        return f"{title}\n\nАктивных тренировок не найдено."

    lines = [title]

    for item in items:
        lines.append("")
        lines.append(format_planned_workout(item))

        exercises = item.get("exercises") or []
        if not exercises:
            lines.append("")
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


def _period_from_scope(action: dict) -> tuple[str | None, str | None]:
    scope = action.get("scope")

    if scope == "all":
        return "1900-01-01", "2999-12-31"

    if scope == "future":
        return _today_iso(), "2999-12-31"

    if scope == "current_week":
        today = date.today()
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()

    if scope == "next_week":
        today = date.today()
        start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()

    if scope == "month":
        today = date.today()
        return date(today.year, today.month, 1).isoformat(), _month_end_iso()

    if action.get("start_date") and action.get("end_date"):
        return action.get("start_date"), action.get("end_date")

    return None, None


def _looks_like_dangerous_history_delete(action: dict) -> bool:
    summary = (action.get("summary") or "").lower()
    return "истори" in summary and "план" not in summary


async def _preview_cancel_planned(
    telegram_user_id: str | None,
    action: dict,
    source_text: str,
) -> str:
    from app.db import (
        create_fitness_pending_decision,
        get_planned_workouts_in_period,
    )

    if _looks_like_dangerous_history_delete(action):
        return (
            "Это похоже на удаление фактической истории тренировок. "
            "Я не буду удалять историю автоматически."
        )

    start_date, end_date = _period_from_scope(action)

    if not start_date or not end_date:
        return "Понял, что нужно отменить плановые тренировки, но не понял период."

    items = await get_planned_workouts_in_period(
        telegram_user_id=telegram_user_id,
        start_date=start_date,
        end_date=end_date,
        include_cancelled=False,
    )

    active_items = [
        item for item in items
        if (item.get("workout") or {}).get("status") == "planned"
    ]

    if not active_items:
        if action.get("scope") == "all":
            return "Активных плановых тренировок не найдено."
        if action.get("scope") == "future":
            return "Активных будущих плановых тренировок не найдено."
        return f"За период {start_date} — {end_date} активных плановых тренировок не найдено."

    await create_fitness_pending_decision(
        telegram_user_id=telegram_user_id,
        decision_type="confirm_cancel_planned_period",
        context={
            "start_date": start_date,
            "end_date": end_date,
            "source_text": source_text,
            "scope": action.get("scope"),
        },
        source_text=source_text,
    )

    if action.get("scope") == "all":
        preview_title = "Будут отменены все активные плановые тренировки"
    elif action.get("scope") == "future":
        preview_title = f"Будут отменены плановые тренировки от {start_date} и дальше"
    else:
        preview_title = f"Будут отменены плановые тренировки {start_date} — {end_date}"

    preview = format_period_plan(
        active_items,
        title=preview_title,
    )

    return (
        preview
        + "\n\nФактическую историю тренировок не трогаю.\n"
        + "Подтверди обычным текстом: “да, отмени” или “отмена”."
    )


def _extract_exercise_names_for_replace(action: dict) -> tuple[str | None, str | None]:
    old_name = action.get("old_exercise_name")
    new_name = action.get("new_exercise_name")

    if old_name:
        old_name = str(old_name).strip()
    if new_name:
        new_name = str(new_name).strip()

    return old_name or None, new_name or None


async def _replace_exercise(
    telegram_user_id: str | None,
    action: dict,
    source_text: str,
) -> str:
    from app.db import replace_exercise_in_planned_workout

    target_date = action.get("target_date") or _today_iso()
    old_name, new_name = _extract_exercise_names_for_replace(action)

    if not old_name or not new_name:
        return "Понял, что нужно заменить упражнение, но не понял что на что менять."

    result = await replace_exercise_in_planned_workout(
        telegram_user_id=telegram_user_id,
        target_date=target_date,
        old_exercise_name=old_name,
        new_exercise_name=new_name,
        source_text=source_text,
    )

    if not result.get("ok"):
        exercises = result.get("available_exercises") or []
        if exercises:
            lines = [
                f"Не нашёл “{old_name}” в тренировке на {target_date}.",
                "",
                "Сейчас в тренировке:",
            ]
            for i, name in enumerate(exercises, start=1):
                lines.append(f"{i}. {name}")
            return "\n".join(lines)

        return result.get("message") or "Не смог заменить упражнение."

    return (
        f"Заменил упражнение в тренировке на {target_date}.\n\n"
        f"Было: {result.get('old_exercise_name')}\n"
        f"Стало: {result.get('new_exercise_name')}\n"
        "Подходы, повторы, вес и заметки сохранил."
    )






async def _set_selected_planned_workout_context(
    telegram_user_id: str | None,
    item: dict | None,
    source_text: str | None = None,
) -> None:
    if not item:
        return

    workout = item.get("workout") or {}
    workout_id = workout.get("id")
    planned_date = workout.get("planned_date")

    if hasattr(planned_date, "isoformat"):
        planned_date = planned_date.isoformat()

    if not workout_id:
        return

    from app.db import create_fitness_pending_decision

    await create_fitness_pending_decision(
        telegram_user_id=telegram_user_id,
        decision_type="selected_planned_workout_context",
        context={
            "planned_workout_id": int(workout_id),
            "target_date": planned_date,
            "title": workout.get("title"),
            "focus": workout.get("focus"),
            "focus_label": workout.get("focus_label"),
        },
        source_text=source_text,
    )


async def _get_selected_planned_workout_context(
    telegram_user_id: str | None,
) -> dict:
    from app.db import get_latest_fitness_pending_decision

    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if not pending:
        return {}

    if pending.get("decision_type") != "selected_planned_workout_context":
        return {}

    return pending.get("context_json") or {}


def _apply_selected_context_to_action(action: dict, selected_context: dict) -> dict:
    if not selected_context:
        return action

    action = dict(action)

    if not action.get("planned_workout_id") and selected_context.get("planned_workout_id"):
        action["planned_workout_id"] = selected_context.get("planned_workout_id")

    if not action.get("target_date") and selected_context.get("target_date"):
        action["target_date"] = selected_context.get("target_date")

    return action


def _format_available_exercises_for_edit(message: str, exercises: list[str] | None = None) -> str:
    lines = [message]

    if exercises:
        lines.append("")
        lines.append("Сейчас в тренировке:")
        for i, name in enumerate(exercises, start=1):
            lines.append(f"{i}. {name}")

    return "\n".join(lines)


def _target_date_or_today(action: dict) -> str:
    return action.get("target_date") or _today_iso()

async def execute_planned_workout_action(
    telegram_user_id: str | None,
    action: dict,
    source_text: str,
) -> str | None:
    """
    Executes structured planning action from planned_workout_parser.py.
    Returns None when action is not applicable and old router may continue.
    """
    from app.db import (
        cleanup_empty_planned_workouts,
        get_next_planned_workouts,
        get_planned_workouts_in_period,
        get_today_planned_workout,
        move_planned_workouts_between_dates,
        create_fitness_pending_decision,
    )

    action_name = action.get("action")
    confidence = float(action.get("confidence") or 0)

    if action_name == "unknown" or confidence < 0.55:
        return None

    selected_context = await _get_selected_planned_workout_context(telegram_user_id)

    edit_action_names = {
        "enter_edit_mode",
        "add_exercise_to_planned_workout",
        "remove_exercise_from_planned_workout",
        "replace_exercise",
        "reorder_exercise",
        "update_exercise_params",
    }

    if action_name in edit_action_names:
        action = _apply_selected_context_to_action(action, selected_context)

    if action_name == "cleanup_empty_planned":
        count = await cleanup_empty_planned_workouts(telegram_user_id=telegram_user_id)
        return (
            "Очистил пустые плановые тренировки.\n"
            f"Отменено: {count}\n"
            "Фактическую историю не трогал."
        )

    if action_name == "show_next_workout":
        items = await get_next_planned_workouts(telegram_user_id=telegram_user_id, limit=5)
        first_date = (items[0].get("workout") or {}).get("planned_date") if items else None
        same_day = [
            item for item in items
            if (item.get("workout") or {}).get("planned_date") == first_date
        ]

        if len(same_day) == 1:
            await _set_selected_planned_workout_context(
                telegram_user_id=telegram_user_id,
                item=same_day[0],
                source_text=source_text,
            )

        if action.get("include_weights"):
            return await _format_workouts_with_weights(
                telegram_user_id=telegram_user_id,
                items=same_day,
                title="Следующая тренировка с весами:",
            )

        return _format_next_workouts(items, include_weights=False)

    if action_name == "show_workout_on_date":
        target_date = action.get("target_date") or _today_iso()

        if action.get("include_weights"):
            data = await get_today_planned_workout(telegram_user_id, target_date)
            if not data:
                return f"На {target_date} активная плановая тренировка не найдена."

            await _set_selected_planned_workout_context(
                telegram_user_id=telegram_user_id,
                item=data,
                source_text=source_text,
            )

            return await _format_workouts_with_weights(
                telegram_user_id=telegram_user_id,
                items=[data],
                title=f"Тренировка на {target_date} с весами:",
            )

        items = await get_planned_workouts_in_period(
            telegram_user_id=telegram_user_id,
            start_date=target_date,
            end_date=target_date,
            include_cancelled=False,
        )

        active_items = [
            item for item in items
            if (item.get("workout") or {}).get("status") == "planned"
        ]

        if len(active_items) == 1:
            await _set_selected_planned_workout_context(
                telegram_user_id=telegram_user_id,
                item=active_items[0],
                source_text=source_text,
            )

        return _format_workouts_on_date(items, target_date=target_date)

    if action_name == "show_period_plan":
        scope = action.get("scope") or "current_week"
        start_date, end_date = _period_from_scope({"scope": scope})
        if not start_date or not end_date:
            return None

        items = await get_planned_workouts_in_period(
            telegram_user_id=telegram_user_id,
            start_date=start_date,
            end_date=end_date,
            include_cancelled=False,
        )
        return format_period_plan(items, title=f"План {start_date} — {end_date}")

    if action_name == "enter_edit_mode":
        from app.db import get_best_planned_workout_for_edit

        selected = await get_best_planned_workout_for_edit(
            telegram_user_id=telegram_user_id,
            target_date=action.get("target_date"),
            planned_workout_id=action.get("planned_workout_id"),
        )

        if not selected.get("ok"):
            return selected.get("message") or "Не нашёл активную плановую тренировку для редактирования."

        item = selected.get("workout")
        await _set_selected_planned_workout_context(
            telegram_user_id=telegram_user_id,
            item=item,
            source_text=source_text,
        )

        return (
            "Ок, редактируем эту тренировку:\n\n"
            + format_planned_workout(item)
            + "\n\nЧто изменить?"
        )

    if action_name == "cancel_planned_workouts":
        return await _preview_cancel_planned(
            telegram_user_id=telegram_user_id,
            action=action,
            source_text=source_text,
        )

    if action_name == "replace_exercise":
        return await _replace_exercise(
            telegram_user_id=telegram_user_id,
            action=action,
            source_text=source_text,
        )

    if action_name == "move_workout":
        source_date = action.get("source_date")
        target_date = action.get("target_date")
        if not source_date or not target_date:
            return "Понял, что нужно перенести тренировку, но не понял дату-источник или дату-назначение."

        moved = await move_planned_workouts_between_dates(
            telegram_user_id=telegram_user_id,
            source_date=source_date,
            target_date=target_date,
            source_text=source_text,
            mode="move",
        )
        return f"Перенёс плановые тренировки с {source_date} на {target_date}.\nПеренесено: {moved}"

    if action_name == "copy_workout":
        source_date = action.get("source_date")
        target_date = action.get("target_date")
        if not source_date or not target_date:
            return "Понял, что нужно скопировать тренировку, но не понял дату-источник или дату-назначение."

        copied = await move_planned_workouts_between_dates(
            telegram_user_id=telegram_user_id,
            source_date=source_date,
            target_date=target_date,
            source_text=source_text,
            mode="copy",
        )
        return f"Скопировал плановые тренировки с {source_date} на {target_date}.\nСоздано копий: {copied}"

    if action_name == "create_custom_workout":
        target_date = action.get("target_date") or _today_iso()

        if action.get("has_workout_details"):
            return await create_custom_workout_from_details(
                telegram_user_id=telegram_user_id,
                text=source_text,
                target_date=target_date,
            )

        await create_fitness_pending_decision(
            telegram_user_id=telegram_user_id,
            decision_type="awaiting_custom_workout_details",
            context={
                "target_date": target_date,
                "source_text": source_text,
            },
            source_text=source_text,
        )

        return f"Ок, добавим тренировку на {target_date}. Какие упражнения будем делать?"

    if action_name == "add_exercise_to_planned_workout":
        from app.db import add_exercise_to_planned_workout

        result = await add_exercise_to_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=action.get("planned_workout_id"),
            target_date=action.get("target_date"),
            exercise_name=action.get("exercise_name"),
            exercise_position=action.get("exercise_position"),
            position_mode=action.get("position_mode"),
            anchor_exercise_name=action.get("anchor_exercise_name"),
            target_sets=action.get("target_sets"),
            target_reps_min=action.get("target_reps_min"),
            target_reps_max=action.get("target_reps_max"),
            target_reps_text=action.get("target_reps_text"),
            target_weight_kg=action.get("target_weight_kg"),
            source_text=source_text,
        )

        if not result.get("ok"):
            return _format_available_exercises_for_edit(
                result.get("message") or "Не смог добавить упражнение.",
                result.get("available_exercises"),
            )

        return (
            f"Добавил упражнение в плановую тренировку.\n\n"
            f"{result.get('exercise_order')}. {result.get('exercise_name')}"
        )

    if action_name == "remove_exercise_from_planned_workout":
        from app.db import remove_exercise_from_planned_workout

        result = await remove_exercise_from_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=action.get("planned_workout_id"),
            target_date=action.get("target_date"),
            exercise_name=action.get("exercise_name"),
            exercise_position=action.get("exercise_position"),
            position_mode=action.get("position_mode"),
            source_text=source_text,
        )

        if not result.get("ok"):
            return _format_available_exercises_for_edit(
                result.get("message") or "Не смог удалить упражнение.",
                result.get("available_exercises"),
            )

        return f"Удалил упражнение из плановой тренировки: {result.get('removed_exercise_name')}"

    if action_name == "reorder_exercise":
        from app.db import reorder_exercise_in_planned_workout

        result = await reorder_exercise_in_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=action.get("planned_workout_id"),
            target_date=action.get("target_date"),
            exercise_name=action.get("exercise_name"),
            exercise_position=action.get("exercise_position"),
            new_position=action.get("new_position"),
            position_mode=action.get("position_mode"),
            anchor_exercise_name=action.get("anchor_exercise_name"),
            source_text=source_text,
        )

        if not result.get("ok"):
            return _format_available_exercises_for_edit(
                result.get("message") or "Не смог изменить порядок упражнения.",
                result.get("available_exercises"),
            )

        return (
            f"Изменил порядок упражнения.\n\n"
            f"{result.get('exercise_name')} теперь на позиции {result.get('new_position')}."
        )

    if action_name == "update_exercise_params":
        from app.db import update_exercise_params_in_planned_workout

        result = await update_exercise_params_in_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=action.get("planned_workout_id"),
            target_date=action.get("target_date"),
            exercise_name=action.get("exercise_name"),
            exercise_position=action.get("exercise_position"),
            target_sets=action.get("target_sets"),
            target_reps_min=action.get("target_reps_min"),
            target_reps_max=action.get("target_reps_max"),
            target_reps_text=action.get("target_reps_text"),
            target_weight_kg=action.get("target_weight_kg"),
            source_text=source_text,
        )

        if not result.get("ok"):
            return _format_available_exercises_for_edit(
                result.get("message") or "Не смог изменить параметры упражнения.",
                result.get("available_exercises"),
            )

        updates = result.get("updates") or {}
        parts = []
        if updates.get("target_sets") is not None:
            parts.append(f"подходы: {updates.get('target_sets')}")
        if updates.get("target_reps_min") is not None and updates.get("target_reps_max") is not None:
            if updates.get("target_reps_min") == updates.get("target_reps_max"):
                parts.append(f"повторы: {updates.get('target_reps_min')}")
            else:
                parts.append(f"повторы: {updates.get('target_reps_min')}-{updates.get('target_reps_max')}")
        elif updates.get("target_reps_min") is not None:
            parts.append(f"повторы: {updates.get('target_reps_min')}")
        if updates.get("target_reps_text") is not None:
            parts.append(f"повторы: {updates.get('target_reps_text')}")
        if updates.get("target_weight_kg") is not None:
            parts.append(f"вес: {updates.get('target_weight_kg'):g} кг")

        details = ", ".join(parts) if parts else "параметры обновлены"

        return (
            f"Изменил параметры упражнения: {result.get('exercise_name')}.\n"
            f"{details}"
        )

    return None
