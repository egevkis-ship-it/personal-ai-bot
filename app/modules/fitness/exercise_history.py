from __future__ import annotations
import json
import re
from datetime import date

from app.modules.fitness.exercise_normalizer import (
    normalize_exercise_name,
    get_exercise_title,
    possible_matches,
)
from app.modules.fitness.formatter import format_planned_workout


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def looks_like_history_request(text: str) -> bool:
    t = (text or "").lower().replace("ё", "е")
    return any(x in t for x in [
        "что я делал",
        "какой вес",
        "какие веса",
        "в прошлый раз",
        "прошлый раз",
        "позапрошлый",
        "последние 3",
        "последние три",
        "история",
        "раньше",
        "подбери веса",
        "с весами",
        "веса ставить",
    ])


def extract_limit(text: str) -> int:
    t = (text or "").lower()
    if "последние 3" in t or "последние три" in t:
        return 3
    if "прошлый и позапрошлый" in t or "позапрош" in t:
        return 2
    return 3


def parse_exercise_history_request(text: str, active_session: dict | None = None) -> dict | None:
    if not looks_like_history_request(text):
        return None

    t = (text or "").lower().replace("ё", "е")

    if any(x in t for x in [
        "сегодняшнюю тренировку с вес",
        "тренировку с вес",
        "подбери веса",
        "веса ставить сегодня",
    ]):
        return {
            "action": "today_workout_with_weights",
            "confidence": 0.9,
            "limit": 2 if "прошл" in t else 3,
            "exercise_name": None,
        }

    match = re.search(
        r"(?:на|по)\s+([а-яa-z0-9\s\-]+?)(?:\?|$| в прошл| прошл| последние| позапрош)",
        t,
    )
    exercise_name = match.group(1).strip() if match else None

    if not exercise_name and active_session and any(x in t for x in [
        "на жиме", "на махах", "на тяге", "по жиму", "по махам", "по тяге"
    ]):
        exercise_name = active_session.get("current_exercise")

    if exercise_name:
        return {
            "action": "exercise_history",
            "confidence": 0.8,
            "limit": extract_limit(text),
            "exercise_name": exercise_name,
        }

    system_prompt = f"""
Ты parser запросов истории упражнений фитнес-ассистента.

Активная сессия:
{json.dumps(active_session or {}, ensure_ascii=False)}

Пользователь:
{text}

Верни строго JSON:
{{
  "action": "exercise_history | today_workout_with_weights | unknown",
  "confidence": 0.0,
  "exercise_name": null,
  "limit": 3
}}

Правила:
- "дай сегодняшнюю тренировку с весами", "подбери веса для сегодняшней тренировки" => today_workout_with_weights.
- "что я делал на жиме гантелей сидя в прошлый раз" => exercise_history.
- "покажи прошлый и позапрошлый жим" => exercise_history, limit=2.
- "последние 3 раза по махам" => exercise_history, limit=3.
- Если упражнение не указано, но в активной сессии есть current_exercise, можно использовать его.
Ответ только JSON.
"""

    from app.ai import client

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    parsed = safe_json_loads(response.choices[0].message.content or "{}")

    if not parsed or parsed.get("action") == "unknown":
        return None

    return parsed


def format_set_line(item: dict) -> str:
    weight = item.get("weight_kg")
    reps = item.get("reps")
    set_number = item.get("set_number")

    if weight is not None and reps is not None:
        return f"{set_number}) {weight:g} кг × {reps}"
    if reps is not None:
        return f"{set_number}) {reps} повторений"
    return f"{set_number}) без данных"


def format_exercise_history(history: list[dict], exercise_title: str, limit: int = 3) -> str:
    if not history:
        return f"{exercise_title}: истории по этому упражнению пока нет."

    labels = ["Прошлый раз", "Позапрошлый раз", "Третий последний раз"]

    lines = [f"{exercise_title} — последние выполнения:"]

    for i, workout in enumerate(history[:limit]):
        label = labels[i] if i < len(labels) else f"{i + 1}-й последний раз"
        lines.append("")
        lines.append(f"{label} — {workout.get('workout_date')}:")
        for item in workout.get("sets") or []:
            lines.append(format_set_line(item))

    if len(history) >= 2:
        last_sets = history[0].get("sets") or []
        prev_sets = history[1].get("sets") or []

        last_top = max([s.get("weight_kg") or 0 for s in last_sets], default=0)
        prev_top = max([s.get("weight_kg") or 0 for s in prev_sets], default=0)

        lines.append("")
        lines.append("Сравнение:")

        if last_top > prev_top:
            lines.append(f"- В прошлый раз максимальный вес был выше: {last_top:g} кг против {prev_top:g} кг.")
        elif last_top < prev_top:
            lines.append(f"- В прошлый раз вес был ниже: {last_top:g} кг против {prev_top:g} кг. Возможно, был откат.")
        elif last_top:
            lines.append(f"- Максимальный вес сохранился: {last_top:g} кг.")

    return "\n".join(lines)


def target_line(exercise: dict) -> str:
    sets = exercise.get("target_sets")
    reps_text = exercise.get("target_reps_text")
    reps_min = exercise.get("target_reps_min")
    reps_max = exercise.get("target_reps_max")
    weight = exercise.get("target_weight_kg")

    parts = []

    if sets and reps_text:
        parts.append(f"{sets}×{reps_text}")
    elif sets and reps_min and reps_max:
        parts.append(f"{sets}×{reps_min}-{reps_max}")
    elif sets and reps_min:
        parts.append(f"{sets}×{reps_min}")
    elif reps_text:
        parts.append(str(reps_text))

    if weight is not None:
        parts.append(f"плановый вес {weight:g} кг")

    return ", ".join(parts)


def recommendation_from_history(history: list[dict], planned_weight=None) -> str:
    if planned_weight is not None:
        return f"Ориентир: плановый вес {planned_weight:g} кг."

    if not history:
        return "Ориентир: истории нет, вес подбери вручную."

    last_sets = history[0].get("sets") or []
    weights = [s.get("weight_kg") for s in last_sets if s.get("weight_kg") is not None]

    if not weights:
        return "Ориентир: по прошлому разу вес не записан."

    top = max(weights)

    if len(history) >= 2:
        prev_sets = history[1].get("sets") or []
        prev_weights = [s.get("weight_kg") for s in prev_sets if s.get("weight_kg") is not None]
        prev_top = max(prev_weights) if prev_weights else None

        if prev_top is not None and top < prev_top:
            return f"Ориентир: {top:g}–{prev_top:g} кг. В прошлый раз был вес ниже позапрошлого."

    return f"Ориентир: около {top:g} кг."


async def handle_exercise_history_request(
    telegram_user_id: str | None,
    text: str,
    active_session: dict | None = None,
) -> str | None:
    from app.db import get_today_planned_workout, get_recent_exercise_history

    parsed = parse_exercise_history_request(text, active_session=active_session)

    if not parsed:
        return None

    action = parsed.get("action")
    confidence = float(parsed.get("confidence") or 0)

    if confidence < 0.55:
        return None

    limit = int(parsed.get("limit") or 3)

    if action == "exercise_history":
        exercise_name = parsed.get("exercise_name") or (active_session or {}).get("current_exercise")

        if not exercise_name:
            return "Не понял, по какому упражнению показать историю."

        normalized = normalize_exercise_name(exercise_name, context=active_session or {})
        exercise_key = normalized.get("exercise_key")

        if not exercise_key:
            matches = possible_matches(exercise_name)
            if matches:
                lines = ["Уточни, какое упражнение имеешь в виду:"]
                for m in matches:
                    lines.append(f"- {m['canonical_ru']}")
                return "\n".join(lines)
            return f"Не смог сопоставить упражнение: {exercise_name}"

        history = await get_recent_exercise_history(
            telegram_user_id=telegram_user_id,
            exercise_key=exercise_key,
            limit_workouts=limit,
        )

        return format_exercise_history(
            history=history,
            exercise_title=get_exercise_title(exercise_key, exercise_name),
            limit=limit,
        )

    if action == "today_workout_with_weights":
        today = date.today().isoformat()
        data = await get_today_planned_workout(telegram_user_id, today)

        if not data:
            return "На сегодня активная плановая тренировка не найдена."

        exercises = data.get("exercises") or []

        lines = [
            "Сегодняшняя тренировка с весами:",
            "",
            format_planned_workout(data),
            "",
            "Весовые ориентиры:",
        ]

        for i, exercise in enumerate(exercises, start=1):
            name = exercise.get("exercise_name") or f"Упражнение {i}"
            normalized = normalize_exercise_name(name, context=active_session or {})
            exercise_key = normalized.get("exercise_key")

            planned_weight = exercise.get("target_weight_kg")
            target = target_line(exercise)

            lines.append("")
            lines.append(f"{i}. {name}")
            if target:
                lines.append(f"План: {target}")

            if not exercise_key:
                lines.append("История: не смог сопоставить упражнение.")
                continue

            history = await get_recent_exercise_history(
                telegram_user_id=telegram_user_id,
                exercise_key=exercise_key,
                limit_workouts=2,
            )

            if history:
                lines.append("История:")
                for idx, h in enumerate(history[:2]):
                    label = "Прошлый раз" if idx == 0 else "Позапрошлый раз"
                    set_text = ", ".join(
                        [
                            f"{s.get('weight_kg'):g}×{s.get('reps')}"
                            for s in h.get("sets") or []
                            if s.get("weight_kg") is not None and s.get("reps") is not None
                        ]
                    )
                    lines.append(f"- {label}, {h.get('workout_date')}: {set_text or 'нет весов'}")
            else:
                lines.append("История: пока нет.")

            lines.append(recommendation_from_history(history, planned_weight=planned_weight))

        return "\n".join(lines)

    return None
