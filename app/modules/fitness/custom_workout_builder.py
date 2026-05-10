from __future__ import annotations

import json
from datetime import date

from app.modules.fitness.exercise_normalizer import normalize_exercise_name


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def format_planned_sets_for_notes(planned_sets: list[dict] | None) -> str | None:
    if not planned_sets:
        return None

    lines = ["planned_sets:"]
    for item in planned_sets:
        set_number = item.get("set_number")
        weight = item.get("weight_kg")
        reps = item.get("reps")

        if weight is not None and reps is not None:
            lines.append(f"{set_number}) {weight:g} кг × {reps}")
        elif weight is not None:
            lines.append(f"{set_number}) {weight:g} кг")
        elif reps is not None:
            lines.append(f"{set_number}) {reps} повторений")
        else:
            lines.append(f"{set_number}) без параметров")

    return "\n".join(lines)


def normalize_custom_workout_payload(payload: dict, target_date: str, source_text: str) -> dict:
    workout = payload.get("workout") or {}
    raw_exercises = workout.get("exercises") or []

    exercises = []

    for i, raw in enumerate(raw_exercises, start=1):
        name = (
            raw.get("exercise_name")
            or raw.get("name")
            or raw.get("title")
            or raw.get("exercise")
        )

        if not name:
            continue

        normalized = normalize_exercise_name(name)
        canonical_name = normalized.get("canonical_ru") or name

        planned_sets = raw.get("planned_sets") or raw.get("sets_detail") or []
        planned_sets_notes = format_planned_sets_for_notes(planned_sets)

        notes_parts = []
        if raw.get("notes"):
            notes_parts.append(str(raw.get("notes")))
        if planned_sets_notes:
            notes_parts.append(planned_sets_notes)

        exercises.append(
            {
                "exercise_order": raw.get("exercise_order") or i,
                "exercise_name": canonical_name,
                "target_sets": raw.get("target_sets"),
                "target_reps_min": raw.get("target_reps_min"),
                "target_reps_max": raw.get("target_reps_max"),
                "target_reps_text": raw.get("target_reps_text"),
                "target_weight_kg": raw.get("target_weight_kg"),
                "notes": "\n".join(notes_parts) if notes_parts else None,
            }
        )

    focus_label = workout.get("focus_label") or workout.get("focus") or "кастомная"
    title = workout.get("title") or f"Кастомная тренировка — {focus_label}"

    return {
        "plan_name": payload.get("plan_name") or "Ad hoc workout",
        "period_type": "day",
        "start_date": target_date,
        "end_date": target_date,
        "source_text": source_text,
        "notes": payload.get("summary") or "Created from custom workout details",
        "planned_workouts": [
            {
                "planned_date": target_date,
                "weekday": None,
                "sequence_number": 1,
                "is_floating": False,
                "title": title,
                "focus": workout.get("focus"),
                "focus_label": focus_label,
                "workout_type": "custom",
                "status": "planned",
                "notes": workout.get("notes"),
                "exercises": exercises,
            }
        ],
    }


def format_custom_workout_preview(payload: dict) -> str:
    workout = payload.get("workout") or {}
    exercises = workout.get("exercises") or []

    if not exercises:
        return "Я не смог выделить упражнения из сообщения."

    lines = ["Я понял тренировку так:"]

    for i, raw in enumerate(exercises, start=1):
        name = raw.get("exercise_name") or raw.get("name") or f"Упражнение {i}"
        normalized = normalize_exercise_name(name)
        canonical_name = normalized.get("canonical_ru") or name

        target_sets = raw.get("target_sets")
        reps_text = raw.get("target_reps_text")
        reps_min = raw.get("target_reps_min")
        reps_max = raw.get("target_reps_max")
        weight = raw.get("target_weight_kg")
        planned_sets = raw.get("planned_sets") or []

        line = f"{i}. {canonical_name}"

        details = []
        if target_sets and reps_text:
            details.append(f"{target_sets}×{reps_text}")
        elif target_sets and reps_min and reps_max:
            details.append(f"{target_sets}×{reps_min}-{reps_max}")
        elif target_sets and reps_min:
            details.append(f"{target_sets}×{reps_min}")
        elif reps_text:
            details.append(str(reps_text))

        if weight is not None:
            details.append(f"{weight:g} кг")

        if details:
            line += " — " + ", ".join(details)

        lines.append(line)

        for item in planned_sets:
            set_number = item.get("set_number")
            set_weight = item.get("weight_kg")
            set_reps = item.get("reps")
            if set_weight is not None and set_reps is not None:
                lines.append(f"   {set_number}) {set_weight:g} кг × {set_reps}")
            elif set_weight is not None:
                lines.append(f"   {set_number}) {set_weight:g} кг")
            elif set_reps is not None:
                lines.append(f"   {set_number}) {set_reps} повторений")

        if raw.get("notes"):
            lines.append(f"   Примечание: {raw.get('notes')}")

    return "\n".join(lines)


async def parse_custom_workout_details(text: str, target_date: str | None = None) -> dict:
    """
    Parse a free-form text/voice workout plan into structured planned workout JSON.
    This is used when the bot has already asked: “Какие упражнения будем делать?”
    """
    from app.ai import client

    target_date = target_date or date.today().isoformat()

    system_prompt = f"""
Ты parser сложной плановой тренировки для фитнес-бота.

Дата тренировки: {target_date}

Пользователь может диктовать живым языком:
- несколько упражнений подряд
- подходы, повторы, веса
- один вес на все подходы
- разные веса и повторы по подходам
- "до отказа"
- упражнение без параметров
- разговорные формы: "пожмем", "поприседаем", "сделаем тягу"

Верни строго JSON без markdown:

{{
  "confidence": 0.0,
  "summary": "",
  "plan_name": "Ad hoc workout",
  "workout": {{
    "title": "Кастомная тренировка",
    "focus": null,
    "focus_label": null,
    "notes": null,
    "exercises": [
      {{
        "exercise_order": 1,
        "exercise_name": "Жим штанги лёжа",
        "target_sets": 4,
        "target_reps_min": 15,
        "target_reps_max": null,
        "target_reps_text": null,
        "target_weight_kg": 80,
        "planned_sets": [],
        "notes": null
      }}
    ]
  }}
}}

Правила извлечения:

1. "Жим лежа 4 подхода по 15 раз 80 кг":
target_sets=4, target_reps_min=15, target_weight_kg=80.

2. "4 по 8-10":
target_sets=4, target_reps_min=8, target_reps_max=10.

3. Разные веса и повторы:
"Жим в тренажере 4 подхода. Первый вес 30, второй 20, третий 10, четвертый 48.
Первый 6 раз, второй 8, третий 10, четвертый 18."
=>
planned_sets:
[
  {{"set_number":1,"weight_kg":30,"reps":6}},
  {{"set_number":2,"weight_kg":20,"reps":8}},
  {{"set_number":3,"weight_kg":10,"reps":10}},
  {{"set_number":4,"weight_kg":48,"reps":18}}
]

4. "до отказа":
target_reps_text="до отказа".

5. Если параметры не указаны — упражнение всё равно добавь, параметры оставь null.

6. Нормализуй очевидные упражнения:
- пожмем на скамье лежа => Жим штанги лёжа
- жим / жим штанги / жим лежа => Жим штанги лёжа
- жим лежа на наклонной скамье => Жим штанги под углом
- жим в тренажере на грудь => Жим на грудь в тренажёре
- присед / приседания => Приседания со штангой
- становая / становую => Становая тяга
- отжимания / отжимания от пола => Отжимания
- пресс => Пресс подъёмы корпуса, ноги согнуты
- пресс складкой / v складка / v-складка => Пресс V-складка
- велосипед / велотренажер / велотренажёр => Велосипед
- тяга горизонтального блока => Горизонтальная тяга блока
- тяга вертикального блока => Вертикальная тяга
- поприседаем со штангой => Приседания со штангой

7. Если пользователь перечисляет упражнения коротко через пробелы, запятые или союз “и”, каждое известное слово считай отдельным упражнением.
Пример: “жим присед становая отжимания пресс велосипед” =>
1. Жим штанги лёжа
2. Приседания со штангой
3. Становая тяга
4. Отжимания
5. Пресс подъёмы корпуса, ноги согнуты
6. Велосипед

8. Не пропускай слово “пресс”. Если рядом написано “пресс велосипед”, это два отдельных упражнения:
- Пресс подъёмы корпуса, ноги согнуты
- Велосипед

9. Если тренировка смешанная, focus="full_body", focus_label="full body".
Если явно грудь/спина/ноги/плечи — укажи соответствующий focus.

Ответ только JSON.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    payload = safe_json_loads(response.choices[0].message.content or "{}")
    payload["target_date"] = target_date
    return payload


async def create_custom_workout_from_details(
    telegram_user_id: str | None,
    text: str,
    target_date: str,
) -> str:
    from app.db import save_training_plan

    payload = await parse_custom_workout_details(text, target_date=target_date)
    confidence = float(payload.get("confidence") or 0)

    if confidence < 0.55:
        return (
            "Я понял, что ты описываешь тренировку, но не смог достаточно уверенно разобрать упражнения. "
            "Скажи проще: упражнение, подходы, повторы, вес."
        )

    normalized_plan = normalize_custom_workout_payload(
        payload=payload,
        target_date=target_date,
        source_text=text,
    )

    workout = normalized_plan["planned_workouts"][0]
    if not workout.get("exercises"):
        return "Я не смог выделить упражнения. Тренировку не создал."

    plan_id = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name=normalized_plan["plan_name"],
        period_type=normalized_plan["period_type"],
        start_date=normalized_plan["start_date"],
        end_date=normalized_plan["end_date"],
        source_text=normalized_plan["source_text"],
        notes=normalized_plan["notes"],
        planned_workouts=normalized_plan["planned_workouts"],
    )

    preview = format_custom_workout_preview(payload)

    return (
        f"Создал тренировку на {target_date}.\n"
        f"ID плана: {plan_id}\n\n"
        f"{preview}"
    )
