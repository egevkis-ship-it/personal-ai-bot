from collections import defaultdict
from datetime import datetime, date


MONTHS_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

WEEKDAYS_RU = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "суббота",
    6: "воскресенье",
}

STATUS_LABELS = {
    "planned": "запланировано",
    "completed": "выполнено",
    "completed_modified": "выполнено с изменениями",
    "skipped": "пропущено",
    "moved": "перенесено",
    "replaced": "заменено",
    "cancelled": "отменено",
}

COMPLETION_LABELS = {
    "as_planned": "по плану",
    "modified": "с изменениями",
    "shortened": "сокращённо",
    "custom": "кастомная",
    "replacement": "замена плановой",
}


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    return None


def format_human_date(value, include_weekday: bool = True) -> str:
    d = parse_date(value)
    if not d:
        return "без даты"

    base = f"{d.day} {MONTHS_RU[d.month]}"
    if include_weekday:
        return f"{base}, {WEEKDAYS_RU[d.weekday()]}"
    return base


def status_label(status: str | None) -> str:
    if not status:
        return ""
    return STATUS_LABELS.get(status, status)


def completion_label(value: str | None) -> str:
    if not value:
        return ""
    return COMPLETION_LABELS.get(value, value)


def format_number(value) -> str:
    if value is None:
        return ""
    try:
        value = float(value)
        if value.is_integer():
            return str(int(value))
        return str(value)
    except Exception:
        return str(value)


def _format_planned_exercises(exercises: list[dict]) -> list[str]:
    lines = []

    for ex in exercises:
        name = ex.get("exercise_name") or "Упражнение"
        sets = ex.get("target_sets")
        reps_text = ex.get("target_reps_text")
        reps_min = ex.get("target_reps_min")
        reps_max = ex.get("target_reps_max")
        weight = ex.get("target_weight_kg")

        if reps_text:
            target = f"{sets}×{reps_text}" if sets else reps_text
        elif reps_min is not None and reps_max is not None:
            if reps_min == reps_max:
                target = f"{sets}×{reps_min}" if sets else str(reps_min)
            else:
                target = f"{sets}×{reps_min}-{reps_max}" if sets else f"{reps_min}-{reps_max}"
        elif sets:
            target = f"{sets} подходов"
        else:
            target = ""

        if weight is not None:
            if target:
                target += f", {format_number(weight)} кг"
            else:
                target = f"{format_number(weight)} кг"

        order = ex.get("exercise_order")
        prefix = f"{order}. " if order else "- "
        line = f"{prefix}{name}"

        if target:
            line += f" — {target}"

        if ex.get("notes"):
            line += f" ({ex.get('notes')})"

        lines.append(line)

    return lines


def format_planned_workout(data: dict | None, title_prefix: str = "Тренировка") -> str:
    if not data:
        return "Тренировка не найдена."

    workout = data["workout"]
    exercises = data.get("exercises", [])

    title = workout.get("title") or workout.get("focus_label") or title_prefix

    lines = [str(title)]

    if workout.get("planned_date"):
        lines.append(f"Дата: {format_human_date(workout.get('planned_date'))}")
    elif workout.get("is_floating"):
        lines.append("Дата: не привязана, тренировка в очереди")
    else:
        lines.append("Дата: без даты")

    if workout.get("focus_label"):
        lines.append(f"Фокус: {workout.get('focus_label')}")

    if workout.get("status"):
        lines.append(f"Статус: {status_label(workout.get('status'))}")

    if exercises:
        lines.append("")
        lines.append("Упражнения:")
        lines.extend(_format_planned_exercises(exercises))

    return "\n".join(lines)


def format_week_plan(items: list[dict]) -> str:
    if not items:
        return "На эту неделю план не найден."

    today = date.today()

    overdue = []
    current_and_future = []
    floating = []
    done = []

    for item in items:
        workout = item["workout"]
        planned_date = parse_date(workout.get("planned_date"))
        status = workout.get("status")

        if status == "cancelled":
            continue
        if status in ("completed", "completed_modified", "skipped", "replaced"):
            done.append(item)
        elif not planned_date:
            floating.append(item)
        elif planned_date < today and status == "planned":
            overdue.append(item)
        else:
            current_and_future.append(item)

    lines = ["План недели:"]

    def line_for(item: dict) -> str:
        workout = item["workout"]
        date_part = format_human_date(workout.get("planned_date")) if workout.get("planned_date") else "без даты"
        title = workout.get("title") or workout.get("focus_label") or "Тренировка"
        status = status_label(workout.get("status"))
        return f"- {date_part}: {title} — {status}"

    if overdue:
        lines.append("")
        lines.append("Просроченные невыполненные:")
        for item in overdue:
            lines.append(line_for(item))

    if current_and_future:
        lines.append("")
        lines.append("Актуальные:")
        for item in current_and_future:
            lines.append(line_for(item))

    if floating:
        lines.append("")
        lines.append("Очередь без дат:")
        for item in floating:
            lines.append(line_for(item))

    if done:
        lines.append("")
        lines.append("Закрытые / изменённые:")
        for item in done:
            lines.append(line_for(item))

    return "\n".join(lines)


def format_completed_workout(parsed: dict, workout_id: int | None = None, linked_plan_title: str | None = None) -> str:
    workout = parsed.get("completed_workout") or {}
    exercises = workout.get("exercises") or []

    lines = ["Записал тренировку."]

    if workout_id:
        lines.append(f"ID: {workout_id}")

    if linked_plan_title:
        lines.append(f"Связал с планом: {linked_plan_title}")

    if workout.get("workout_date"):
        lines.append(f"Дата: {format_human_date(workout.get('workout_date'))}")

    if workout.get("focus_label"):
        lines.append(f"Фокус: {workout.get('focus_label')}")

    if workout.get("completion_type"):
        lines.append(f"Тип выполнения: {completion_label(workout.get('completion_type'))}")

    if workout.get("bodyweight_kg"):
        lines.append(f"Вес: {format_number(workout.get('bodyweight_kg'))} кг")

    tonnage = 0.0

    for exercise in exercises:
        name = exercise.get("name") or exercise.get("exercise_name") or "Упражнение"
        lines.append("")
        lines.append(f"{name}:")
        for s in exercise.get("sets", []):
            set_number = s.get("set_number")
            weight = s.get("weight_kg")
            reps = s.get("reps")
            rpe = s.get("rpe")

            if weight is not None and reps is not None:
                line = f"{set_number}) {format_number(weight)}×{reps}"
                try:
                    tonnage += float(weight) * int(reps)
                except Exception:
                    pass
            elif reps is not None:
                line = f"{set_number}) {reps} повторений"
            else:
                line = f"{set_number}) записано"

            if rpe is not None:
                line += f" RPE {rpe}"

            lines.append(line)

    if tonnage > 0:
        lines.append("")
        lines.append(f"Тоннаж: {format_number(tonnage)} кг")

    return "\n".join(lines)


def format_measurement(data: dict, measurement_id: int | None = None) -> str:
    m = data.get("body_measurements") or data

    lines = ["Записал замеры."]

    if measurement_id:
        lines.append(f"ID: {measurement_id}")

    if m.get("measurement_date"):
        lines.append(f"Дата: {format_human_date(m.get('measurement_date'))}")

    fields = [
        ("weight_kg", "Вес", "кг"),
        ("waist_cm", "Талия", "см"),
        ("chest_cm", "Грудь", "см"),
        ("hips_cm", "Бёдра", "см"),
        ("arm_cm", "Рука", "см"),
        ("thigh_cm", "Бедро", "см"),
        ("neck_cm", "Шея", "см"),
    ]

    for key, label, unit in fields:
        if m.get(key) is not None:
            lines.append(f"{label}: {format_number(m.get(key))} {unit}")

    return "\n".join(lines)


def format_last_workout(data: dict | None) -> str:
    if not data:
        return "Пока нет записанных тренировок."

    workout = data["workout"]
    sets = data["sets"]

    lines = [
        "Последняя тренировка:",
        f"Дата: {format_human_date(workout.get('workout_date'))}",
    ]

    if workout.get("focus_label"):
        lines.append(f"Фокус: {workout.get('focus_label')}")

    if workout.get("completion_type"):
        lines.append(f"Тип выполнения: {completion_label(workout.get('completion_type'))}")

    if workout.get("bodyweight_kg"):
        lines.append(f"Вес: {format_number(workout.get('bodyweight_kg'))} кг")

    grouped = defaultdict(list)
    for row in sets:
        grouped[row["exercise_name"]].append(row)

    for exercise_name, exercise_sets in grouped.items():
        lines.append("")
        lines.append(f"{exercise_name}:")
        for s in exercise_sets:
            weight = s.get("weight_kg")
            reps = s.get("reps")
            set_number = s.get("set_number")

            if weight is not None and reps is not None:
                lines.append(f"{set_number}) {format_number(weight)}×{reps}")
            elif reps is not None:
                lines.append(f"{set_number}) {reps} повторений")
            else:
                lines.append(f"{set_number}) записано")

    return "\n".join(lines)


def format_last_measurement(row: dict | None) -> str:
    if not row:
        return "Пока нет записанных замеров."

    lines = [
        "Последние замеры:",
        f"Дата: {format_human_date(row.get('measurement_date'))}",
    ]

    fields = [
        ("weight_kg", "Вес", "кг"),
        ("waist_cm", "Талия", "см"),
        ("chest_cm", "Грудь", "см"),
        ("hips_cm", "Бёдра", "см"),
        ("arm_cm", "Рука", "см"),
        ("thigh_cm", "Бедро", "см"),
        ("neck_cm", "Шея", "см"),
    ]

    for key, label, unit in fields:
        if row.get(key) is not None:
            lines.append(f"{label}: {format_number(row.get(key))} {unit}")

    return "\n".join(lines)
