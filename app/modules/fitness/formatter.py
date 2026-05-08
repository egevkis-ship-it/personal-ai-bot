from collections import defaultdict
from app.modules.fitness.utils import format_number


def format_planned_workout(data: dict | None, title_prefix: str = "Тренировка") -> str:
    if not data:
        return "Тренировка не найдена."

    workout = data["workout"]
    exercises = data.get("exercises", [])

    lines = []

    title = workout.get("title") or workout.get("focus_label") or title_prefix
    lines.append(str(title))

    if workout.get("planned_date"):
        lines.append(f"Дата: {workout.get('planned_date')}")
    elif workout.get("is_floating"):
        lines.append("Дата: не привязана, тренировка в очереди")

    if workout.get("focus_label"):
        lines.append(f"Фокус: {workout.get('focus_label')}")

    if workout.get("status"):
        lines.append(f"Статус: {workout.get('status')}")

    if exercises:
        lines.append("")
        lines.append("Упражнения:")
        for ex in exercises:
            name = ex.get("exercise_name") or "Упражнение"
            sets = ex.get("target_sets")
            reps_text = ex.get("target_reps_text")
            reps_min = ex.get("target_reps_min")
            reps_max = ex.get("target_reps_max")

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

            line = f"{ex.get('exercise_order') or ''}. {name}".strip()
            if target:
                line += f" — {target}"
            if ex.get("notes"):
                line += f" ({ex.get('notes')})"
            lines.append(line)

    return "\n".join(lines)


def format_week_plan(items: list[dict]) -> str:
    if not items:
        return "На эту неделю план не найден."

    lines = ["План недели:"]

    for item in items:
        workout = item["workout"]
        date_part = workout.get("planned_date") or "без даты"
        title = workout.get("title") or workout.get("focus_label") or "Тренировка"
        status = workout.get("status") or "planned"
        lines.append(f"- {date_part}: {title} — {status}")

    return "\n".join(lines)


def format_completed_workout(parsed: dict, workout_id: int | None = None, linked_plan_title: str | None = None) -> str:
    workout = parsed.get("completed_workout") or {}
    exercises = workout.get("exercises") or []

    lines = ["Записал тренировку."]

    if workout_id:
        lines.append(f"ID: {workout_id}")

    if linked_plan_title:
        lines.append(f"Связал с планом: {linked_plan_title}")

    if workout.get("focus_label"):
        lines.append(f"Фокус: {workout.get('focus_label')}")

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
                lines.append(f"{set_number}) {format_number(weight)}×{reps}")
                try:
                    tonnage += float(weight) * int(reps)
                except Exception:
                    pass
            elif reps is not None:
                lines.append(f"{set_number}) {reps} повторений")
            else:
                lines.append(f"{set_number}) записано")

            if rpe is not None:
                lines[-1] += f" RPE {rpe}"

    if tonnage > 0:
        lines.append("")
        lines.append(f"Тоннаж: {format_number(tonnage)} кг")

    return "\n".join(lines)


def format_measurement(data: dict, measurement_id: int | None = None) -> str:
    m = data.get("body_measurements") or data

    lines = ["Записал замеры."]

    if measurement_id:
        lines.append(f"ID: {measurement_id}")

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
        f"Дата: {workout.get('workout_date')}",
    ]

    if workout.get("focus_label"):
        lines.append(f"Фокус: {workout.get('focus_label')}")

    if workout.get("completion_type"):
        lines.append(f"Тип выполнения: {workout.get('completion_type')}")

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
        f"Дата: {row.get('measurement_date')}",
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
