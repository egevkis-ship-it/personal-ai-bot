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
    from app.modules.fitness.formatter import format_human_date
    return f"Тренировка на {format_human_date(target_date)}"


def _is_non_empty_workout(item: dict) -> bool:
    return bool((item or {}).get("exercises"))


def _format_workouts_on_date(items: list[dict], target_date: str, include_weights: bool = False) -> str:
    active_items = [
        item for item in (items or [])
        if (item.get("workout") or {}).get("status") == "planned"
    ]

    from app.modules.fitness.formatter import format_human_date as _fhd
    if not active_items:
        return f"На {_fhd(target_date)} активных плановых тренировок не найдено."

    non_empty = [item for item in active_items if _is_non_empty_workout(item)]
    empty = [item for item in active_items if not _is_non_empty_workout(item)]

    ordered = non_empty + empty

    if len(ordered) == 1:
        title = f"{_date_title(target_date)}:"
        if include_weights:
            title = f"{_date_title(target_date)} с весами:"
        return title + "\n\n" + format_planned_workout(ordered[0])

    lines = [f"На {_fhd(target_date)} найдено активных тренировок: {len(ordered)}"]

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

    if scope == "period" and action.get("start_date") and action.get("end_date"):
        return action.get("start_date"), action.get("end_date")

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
        + "Чтобы подтвердить отмену плановых тренировок, напиши: “да”, “отмени”, “отмена”, “отменяй” или “удали”. Чтобы отказаться — “не надо” или “стоп”."
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
    from app.db import replace_exercise_in_planned_workout, get_last_interaction
    from app.modules.fitness.formatter import format_human_date

    target_date = action.get("target_date")

    # Fallback: если дата не пришла — используем последнюю обсуждаемую
    if not target_date and telegram_user_id:
        last = await get_last_interaction(telegram_user_id)
        if last and last.get("current_workout_date"):
            target_date = str(last["current_workout_date"])[:10]

    if not target_date:
        target_date = _today_iso()

    old_name, new_name = _extract_exercise_names_for_replace(action)
    if not old_name or not new_name:
        return "Понял, что нужно заменить упражнение, но не понял что на что менять."

    # Extract new params if mentioned (parser may put them in action.new_*)
    new_params = action.get("new_params") or {}

    result = await replace_exercise_in_planned_workout(
        telegram_user_id=telegram_user_id,
        target_date=target_date,
        old_exercise_name=old_name,
        new_exercise_name=new_name,
        source_text=source_text,
        new_sets=new_params.get("sets"),
        new_reps_min=new_params.get("reps_min"),
        new_reps_max=new_params.get("reps_max"),
        new_weight_kg=new_params.get("weight_kg"),
        reset_params=True,
    )

    if not result.get("ok"):
        exercises = result.get("available_exercises") or []
        if exercises:
            lines = [
                f"Не нашёл «{old_name}» в тренировке на {format_human_date(target_date)}.",
                "",
                "Сейчас в тренировке:",
            ]
            for i, name in enumerate(exercises, start=1):
                lines.append(f"{i}. {name}")
            return "\n".join(lines)
        return result.get("message") or "Не смог заменить упражнение."

    prefix_parts = [
        f"Заменил упражнение в тренировке на {format_human_date(target_date)}.",
        "",
        f"Было: {result.get('old_exercise_name')}",
        f"Стало: {result.get('new_exercise_name')}",
    ]
    if result.get("asked_for_params"):
        prefix_parts.append("")
        prefix_parts.append("⚠️ Подходы/повторы/вес/заметки сброшены (они были от старого упражнения).")
        prefix_parts.append("Скажи параметры: «4×10 80 кг» или «3 по 8-10, без веса».")
    elif result.get("applied_params"):
        prefix_parts.append("Параметры применил из твоего запроса.")

    return await _format_updated_workout_after_edit(
        planned_workout_id=result.get("planned_workout_id"),
        prefix="\n".join(prefix_parts),
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




def _copy_month_range(offset_months: int = 0) -> tuple[date, date]:
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

    return start, next_start - timedelta(days=1)


def _copy_dates_for_weekday_between(weekday: int, start: date, end: date) -> list[str]:
    result = []
    d = start

    while d.weekday() != weekday:
        d += timedelta(days=1)

    while d <= end:
        result.append(d.isoformat())
        d += timedelta(days=7)

    return result


def _copy_next_month_dates_same_weekday(source_date: str, explicit_weekday: int | None = None) -> list[str]:
    src = date.fromisoformat(source_date)
    weekday = explicit_weekday if explicit_weekday is not None else src.weekday()
    start, end = _copy_month_range(1)
    return _copy_dates_for_weekday_between(weekday, start, end)


def _copy_months_dates_same_weekday(
    source_date: str,
    months: int = 1,
    explicit_weekday: int | None = None,
) -> list[str]:
    src = date.fromisoformat(source_date)
    weekday = explicit_weekday if explicit_weekday is not None else src.weekday()

    result = []
    for offset in range(0, max(1, months)):
        start, end = _copy_month_range(offset)
        result.extend(_copy_dates_for_weekday_between(weekday, start, end))

    # Only future/current dates, no duplicates.
    today_s = date.today().isoformat()
    return sorted({d for d in result if d >= today_s})


def _copy_next_weekdays(weekday: int, count: int = 4, start_after: str | None = None) -> list[str]:
    if start_after:
        d = date.fromisoformat(start_after) + timedelta(days=1)
    else:
        d = date.today()

    while d.weekday() != weekday:
        d += timedelta(days=1)

    result = []
    for _ in range(max(1, count)):
        result.append(d.isoformat())
        d += timedelta(days=7)

    return result


def _build_copy_target_dates(action: dict, source_date: str) -> list[str]:
    dates: list[str] = []

    if action.get("target_dates"):
        dates = list(action.get("target_dates") or [])

    elif action.get("target_date"):
        dates = [action["target_date"]]

    else:
        rule = action.get("target_rule")

        if rule == "source_plus_7_days":
            src = date.fromisoformat(source_date)
            dates = [(src + timedelta(days=7)).isoformat()]

        elif rule == "next_month_same_weekday":
            dates = _copy_next_month_dates_same_weekday(
                source_date=source_date,
                explicit_weekday=action.get("weekday"),
            )

        elif rule == "months_same_weekday":
            dates = _copy_months_dates_same_weekday(
                source_date=source_date,
                months=int(action.get("months") or 1),
                explicit_weekday=action.get("weekday"),
            )

        elif rule == "next_weekdays":
            weekday = action.get("weekday")
            if weekday is None:
                dates = []
            else:
                dates = _copy_next_weekdays(
                    weekday=int(weekday),
                    count=int(action.get("count") or 4),
                    start_after=source_date,
                )

    return sorted({d for d in dates if d and d != source_date})

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



def _is_vague_or_placeholder_exercise(name: str | None) -> bool:
    t = (name or "").strip().lower().replace("ё", "е")

    if not t:
        return True

    vague_exact = {
        "упражнение",
        "упражнение 1",
        "упражнение 2",
        "упражнение 3",
        "кастомное упражнение",
        "спина упражнение",
        "спина упражнение 1",
        "спина упражнение 2",
        "спина упражнение 3",
        "грудь упражнение 1",
        "ноги упражнение 1",
        "плечи упражнение 1",
    }

    if t in vague_exact:
        return True

    if "упражнение" in t and any(x in t for x in ["спина", "грудь", "ноги", "плечи"]):
        return True

    return False


def _looks_like_vague_multi_add(action: dict, source_text: str | None) -> bool:
    t = (source_text or "").strip().lower().replace("ё", "е")

    if action.get("action") != "add_exercise_to_planned_workout":
        return False

    if any(x in t for x in ["добавь три", "добавь 3", "три упражнения", "3 упражнения", "несколько упражнений"]):
        return True

    return False


async def _format_updated_workout_after_edit(
    planned_workout_id: int | None,
    prefix: str,
) -> str:
    from app.db import get_planned_workout_by_id

    if not planned_workout_id:
        return prefix

    item = await get_planned_workout_by_id(planned_workout_id)
    if not item:
        return prefix

    return prefix + "\n\nАктуальная тренировка:\n\n" + format_planned_workout(item)


async def _show_selected_workout_if_available(
    telegram_user_id: str | None,
) -> str | None:
    selected_context = await _get_selected_planned_workout_context(telegram_user_id)
    planned_workout_id = selected_context.get("planned_workout_id")

    if not planned_workout_id:
        return None

    from app.db import get_planned_workout_by_id

    item = await get_planned_workout_by_id(planned_workout_id)
    if not item:
        return None

    return "Текущая выбранная тренировка:\n\n" + format_planned_workout(item)



def _only_active_planned_items(items: list[dict]) -> list[dict]:
    result = []
    for item in items or []:
        workout = item.get("workout") or {}
        if workout.get("status") == "planned":
            result.append(item)
    return result


def _format_active_plan_or_empty(items: list[dict], title: str) -> str:
    active_items = _only_active_planned_items(items)
    if not active_items:
        return f"{title}:\n\nАктивных плановых тренировок нет."
    return format_period_plan(active_items, title=title)



def _compound_norm_name(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def _compound_canonical_exercise_name(value: str | None) -> str:
    raw = (value or "").strip()
    norm = _compound_norm_name(raw)

    mapping = {
        "жим": "Жим штанги лёжа",
        "жим штанги": "Жим штанги лёжа",
        "жим штанги лежа": "Жим штанги лёжа",
        "жим лежа": "Жим штанги лёжа",
        "жим гантелей под углом": "Жим гантелей под углом",
        "жим ногами": "Жим ногами",
        "присед": "Приседания со штангой",
        "приседания": "Приседания со штангой",
        "становая": "Становая тяга",
        "становая тяга": "Становая тяга",
        "отжимания": "Отжимания",
        "пресс": "Пресс подъёмы корпуса, ноги согнуты",
        "велосипед": "Велосипед",
    }

    return mapping.get(norm, raw[:1].upper() + raw[1:])


async def _compound_replace_exercise_direct(
    telegram_user_id: str | None,
    selected_context: dict,
    old_name: str | None,
    new_name: str | None,
) -> bool:
    from sqlalchemy import text

    from app.db import AsyncSessionLocal

    if not telegram_user_id or not old_name or not new_name:
        return False

    planned_workout_id = selected_context.get("planned_workout_id")
    target_date = selected_context.get("target_date")

    old_norm = _compound_norm_name(old_name)
    new_canonical = _compound_canonical_exercise_name(new_name)

    async with AsyncSessionLocal() as session:
        if planned_workout_id:
            workout_result = await session.execute(
                text(
                    """
                    SELECT id
                    FROM planned_workouts
                    WHERE telegram_user_id = :telegram_user_id
                      AND id = :planned_workout_id
                      AND status = 'planned'
                    LIMIT 1
                    """
                ),
                {
                    "telegram_user_id": str(telegram_user_id),
                    "planned_workout_id": int(planned_workout_id),
                },
            )
        else:
            workout_result = await session.execute(
                text(
                    """
                    SELECT id
                    FROM planned_workouts
                    WHERE telegram_user_id = :telegram_user_id
                      AND planned_date = :target_date
                      AND status = 'planned'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {
                    "telegram_user_id": str(telegram_user_id),
                    "target_date": target_date,
                },
            )

        workout_row = workout_result.mappings().first()
        if not workout_row:
            return False

        workout_id = int(workout_row["id"])

        exercises_result = await session.execute(
            text(
                """
                SELECT id, exercise_name
                FROM planned_exercises
                WHERE planned_workout_id = :planned_workout_id
                ORDER BY exercise_order, id
                """
            ),
            {"planned_workout_id": workout_id},
        )

        rows = list(exercises_result.mappings())
        match = None

        for row in rows:
            existing = _compound_norm_name(row["exercise_name"])
            if existing == old_norm or old_norm in existing or existing in old_norm:
                match = row
                break

        if not match:
            return False

        await session.execute(
            text(
                """
                UPDATE planned_exercises
                SET exercise_name = :new_name
                WHERE id = :exercise_id
                """
            ),
            {
                "new_name": new_canonical,
                "exercise_id": int(match["id"]),
            },
        )
        await session.commit()

    return True


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
        if not action.get("target_date"):
            selected_reply = await _show_selected_workout_if_available(telegram_user_id)
            if selected_reply is not None:
                return selected_reply

        target_date = action.get("target_date") or _today_iso()

        if action.get("include_weights"):
            data = await get_today_planned_workout(telegram_user_id, target_date)
            if not data:
                from app.modules.fitness.formatter import format_human_date as _fhd2
                return f"На {_fhd2(target_date)} активная плановая тренировка не найдена."

            await _set_selected_planned_workout_context(
                telegram_user_id=telegram_user_id,
                item=data,
                source_text=source_text,
            )

            return await _format_workouts_with_weights(
                telegram_user_id=telegram_user_id,
                items=[data],
                title=f"Тренировка на {_date_title(target_date).removeprefix('Тренировка на ')} с весами:",
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
        start_date, end_date = _period_from_scope(action)
        if not start_date or not end_date:
            return None

        items = await get_planned_workouts_in_period(
            telegram_user_id=telegram_user_id,
            start_date=start_date,
            end_date=end_date,
            include_cancelled=False,
        )

        from app.modules.fitness.formatter import format_human_date
        if scope == "future":
            title = "Все будущие плановые тренировки"
        elif scope == "all":
            title = "Все плановые тренировки"
        elif start_date == "1900-01-01" or end_date == "2999-12-31":
            title = "Плановые тренировки (все доступные)"
        else:
            title = f"План {format_human_date(start_date, include_weekday=False)} — {format_human_date(end_date, include_weekday=False)}"

        return _format_active_plan_or_empty(items, title=title)

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

        if not source_date and selected_context.get("target_date"):
            source_date = selected_context.get("target_date")

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

    if action_name == "copy_period_workouts":
        from app.db import copy_planned_workouts_period

        source_start_date = action.get("source_start_date")
        source_end_date = action.get("source_end_date")
        target_start_date = action.get("target_start_date")
        target_end_date = action.get("target_end_date")
        collision_policy = action.get("collision_policy") or "skip_existing"

        result = await copy_planned_workouts_period(
            telegram_user_id=telegram_user_id,
            source_start_date=source_start_date,
            source_end_date=source_end_date,
            target_start_date=target_start_date,
            target_end_date=target_end_date,
            collision_policy=collision_policy,
            source_text=source_text,
        )

        created = result.get("created") or []
        skipped = result.get("skipped") or []

        lines = [
            f"Скопировал период {source_start_date} — {source_end_date}.",
            "",
            f"Целевой период: {target_start_date} — {target_end_date}",
            "",
            f"Создано тренировок: {len(created)}",
        ]

        for item in created[:20]:
            lines.append(
                f"- {item.get('target_date')}: {item.get('title')} "
                f"(из {item.get('source_date')})"
            )

        if len(created) > 20:
            lines.append(f"- ... ещё {len(created) - 20}")

        if skipped:
            lines.append("")
            lines.append(f"Пропущено: {len(skipped)}")
            for item in skipped[:20]:
                lines.append(f"- {item.get('target_date')}: {item.get('reason')}")

            if len(skipped) > 20:
                lines.append(f"- ... ещё {len(skipped) - 20}")

        return "\n".join(lines)

    if action_name == "compound_edit_workout":
        from app.modules.fitness.planned_workout_editor import fast_parse_workout_edit

        operations = action.get("operations") or []
        applied = []
        failed = []

        for op in operations:
            op_type = op.get("type")

            if op_type == "replace_exercise":
                selected_context = await _get_selected_planned_workout_context(telegram_user_id)

                replaced = await _compound_replace_exercise_direct(
                    telegram_user_id=telegram_user_id,
                    selected_context=selected_context,
                    old_name=op.get("old_name"),
                    new_name=op.get("new_name"),
                )

                if replaced:
                    applied.append(
                        f"замена: {_compound_canonical_exercise_name(op.get('old_name'))} → "
                        f"{_compound_canonical_exercise_name(op.get('new_name'))}"
                    )
                else:
                    failed.append(f"замена: {op.get('old_name')} → {op.get('new_name')}")

            elif op_type == "add_exercise":
                exercise_name = op.get("exercise_name")
                position = op.get("position") or "end"

                if position == "start":
                    sub_text = f"добавь в начало {exercise_name}"
                else:
                    sub_text = f"добавь в конце {exercise_name}"

                sub_action = fast_parse_workout_edit(sub_text)

                if not sub_action:
                    failed.append(f"добавление: {exercise_name}")
                    continue

                result = await execute_planned_workout_action(
                    telegram_user_id=telegram_user_id,
                    action=sub_action,
                    source_text=sub_text,
                )

                if result and (
                    "Добавил упражнение" in result
                    or "Добавил упражнение в плановую тренировку" in result
                    or "Актуальная тренировка:" in result
                    or str(exercise_name or "").lower() in result.lower()
                ):
                    applied.append(f"добавление: {_compound_canonical_exercise_name(exercise_name)}")
                else:
                    failed.append(f"добавление: {exercise_name}")

        selected_context = await _get_selected_planned_workout_context(telegram_user_id)
        target_date = selected_context.get("target_date")

        final_view = None
        if target_date:
            show_action = {
                "action": "show_period_plan",
                "scope": "date",
                "start_date": target_date,
                "end_date": target_date,
            }
            final_view = await execute_planned_workout_action(
                telegram_user_id=telegram_user_id,
                action=show_action,
                source_text=source_text,
            )

        lines = ["Применил групповое редактирование."]

        if applied:
            lines.append("")
            lines.append("Сделано:")
            for item in applied:
                lines.append(f"- {item}")

        if failed:
            lines.append("")
            lines.append("Не получилось:")
            for item in failed:
                lines.append(f"- {item}")

        if final_view:
            lines.append("")
            lines.append(final_view)

        return "\n".join(lines)

    if action_name == "copy_workout":
        from app.db import move_planned_workouts_between_dates, has_active_planned_workout_on_date

        selected_context = await _get_selected_planned_workout_context(telegram_user_id)

        source_date = action.get("source_date")
        if not source_date and action.get("source") == "selected_context":
            source_date = selected_context.get("target_date")

        if not source_date:
            return (
                "Не понял, какую тренировку копировать. "
                "Сначала покажи нужную тренировку, потом скажи: “скопируй эту тренировку на следующую неделю”."
            )

        target_dates = _build_copy_target_dates(action, source_date=source_date)

        if not target_dates:
            return (
                "Не понял, на какие даты копировать тренировку. "
                "Например: “на следующую неделю”, “на следующий месяц”, “на следующие понедельники”."
            )

        created = []
        skipped = []

        for target_date in target_dates:
            if target_date == source_date:
                skipped.append({
                    "target_date": target_date,
                    "reason": "дата совпадает с исходной",
                })
                continue

            if action.get("skip_existing", True):
                already_exists = await has_active_planned_workout_on_date(
                    telegram_user_id=telegram_user_id,
                    target_date=target_date,
                )
                if already_exists:
                    skipped.append({
                        "target_date": target_date,
                        "reason": "уже есть активная плановая тренировка",
                    })
                    continue

            copied_count = await move_planned_workouts_between_dates(
                telegram_user_id=telegram_user_id,
                source_date=source_date,
                target_date=target_date,
                mode="copy",
                source_text=source_text,
            )

            if copied_count:
                created.append({
                    "target_date": target_date,
                    "count": copied_count,
                })
            else:
                skipped.append({
                    "target_date": target_date,
                    "reason": "нет исходной тренировки или на дату уже ничего не скопировано",
                })

        lines = [
            f"Скопировал выбранную тренировку с {source_date}.",
            "",
            f"Создано копий: {sum(x['count'] for x in created)}",
        ]

        for item in created:
            lines.append(f"- {item['target_date']}: создано {item['count']}")

        if skipped:
            lines.append("")
            lines.append("Пропущено:")
            for item in skipped:
                lines.append(f"- {item['target_date']}: {item['reason']}")

        return "\n".join(lines)

    
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

        if _looks_like_vague_multi_add(action, source_text):
            from app.db import create_fitness_pending_decision

            await create_fitness_pending_decision(
                telegram_user_id=telegram_user_id,
                decision_type="awaiting_add_exercises_to_selected_workout",
                context={
                    "planned_workout_id": action.get("planned_workout_id"),
                    "target_date": action.get("target_date"),
                    "source_text": source_text,
                },
                source_text=source_text,
            )

            return "Какие именно упражнения добавить? Перечисли их названиями, и я добавлю в выбранную тренировку."

        if _is_vague_or_placeholder_exercise(action.get("exercise_name")):
            return "Не понял, какое конкретно упражнение добавить. Напиши название упражнения."

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

        prefix = (
            f"Добавил упражнение в плановую тренировку.\n\n"
            f"{result.get('exercise_order')}. {result.get('exercise_name')}"
        )

        return await _format_updated_workout_after_edit(
            planned_workout_id=result.get("planned_workout_id"),
            prefix=prefix,
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

        prefix = f"Удалил упражнение из плановой тренировки: {result.get('removed_exercise_name')}"

        return await _format_updated_workout_after_edit(
            planned_workout_id=result.get("planned_workout_id"),
            prefix=prefix,
        )

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

        prefix = (
            f"Изменил порядок упражнения.\n\n"
            f"{result.get('exercise_name')} теперь на позиции {result.get('new_position')}."
        )

        return await _format_updated_workout_after_edit(
            planned_workout_id=result.get("planned_workout_id"),
            prefix=prefix,
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

        # Repetitions: prefer numeric range, fall back to text — no double field.
        rmin = updates.get("target_reps_min")
        rmax = updates.get("target_reps_max")
        rtext = updates.get("target_reps_text")
        if rmin is not None and rmax is not None:
            if rmin == rmax:
                parts.append(f"повторы: {rmin}")
            else:
                parts.append(f"повторы: {rmin}-{rmax}")
        elif rmin is not None:
            parts.append(f"повторы: {rmin}")
        elif rtext is not None:
            parts.append(f"повторы: {rtext}")

        if updates.get("target_weight_kg") is not None:
            parts.append(f"вес: {updates.get('target_weight_kg'):g} кг")

        details = ", ".join(parts) if parts else "параметры обновлены"

        prefix = (
            f"Изменил параметры упражнения: {result.get('exercise_name')}.\n"
            f"{details}"
        )

        return await _format_updated_workout_after_edit(
            planned_workout_id=result.get("planned_workout_id"),
            prefix=prefix,
        )

    return None
