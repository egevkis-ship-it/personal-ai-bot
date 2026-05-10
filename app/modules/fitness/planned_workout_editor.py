from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.modules.fitness.exercise_normalizer import normalize_exercise_name


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


WEEKDAY_TO_OFFSET = {
    "понедельник": 0,
    "понедельникe": 0,
    "в понедельник": 0,
    "вторник": 1,
    "во вторник": 1,
    "среда": 2,
    "среду": 2,
    "в среду": 2,
    "четверг": 3,
    "в четверг": 3,
    "пятница": 4,
    "пятницу": 4,
    "в пятницу": 4,
    "суббота": 5,
    "субботу": 5,
    "в субботу": 5,
    "воскресенье": 6,
    "в воскресенье": 6,
}


ORDINAL_WORDS = {
    "первый": 1,
    "первым": 1,
    "первая": 1,
    "первое": 1,
    "второй": 2,
    "вторым": 2,
    "вторая": 2,
    "второе": 2,
    "третий": 3,
    "третьим": 3,
    "третья": 3,
    "третье": 3,
    "четвертый": 4,
    "четвёртый": 4,
    "четвертым": 4,
    "четвёртым": 4,
    "четвертая": 4,
    "четвёртая": 4,
    "четвертое": 4,
    "четвёртое": 4,
    "пятый": 5,
    "пятым": 5,
    "пятая": 5,
    "пятое": 5,
    "шестой": 6,
    "шестым": 6,
    "шестая": 6,
    "шестое": 6,
    "седьмой": 7,
    "седьмым": 7,
    "седьмая": 7,
    "седьмое": 7,
    "восьмой": 8,
    "восьмым": 8,
    "восьмая": 8,
    "восьмое": 8,
    "девятый": 9,
    "девятым": 9,
    "девятая": 9,
    "девятое": 9,
    "десятый": 10,
    "десятым": 10,
    "десятая": 10,
    "десятое": 10,
}


def _clean(text: str | None) -> str:
    return (text or "").strip().lower().replace("ё", "е")


def _today() -> date:
    return date.today()


def _today_iso() -> str:
    return _today().isoformat()


def _next_weekday_iso(weekday_index: int) -> str:
    today = _today()
    delta = weekday_index - today.weekday()
    if delta < 0:
        delta += 7
    return (today + timedelta(days=delta)).isoformat()


def _parse_target_date(text: str | None) -> str | None:
    t = _clean(text)

    if "сегодня" in t:
        return _today_iso()

    if "завтра" in t:
        return (_today() + timedelta(days=1)).isoformat()

    for phrase, weekday_index in WEEKDAY_TO_OFFSET.items():
        if phrase in t:
            return _next_weekday_iso(weekday_index)

    m = re.search(
        r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b",
        t,
    )
    if m:
        day = int(m.group(1))
        month = RU_MONTHS[m.group(2)]
        return date(_today().year, month, day).isoformat()

    return None


def _parse_position(text: str | None) -> int | None:
    t = _clean(text)

    m = re.search(r"\b(?:номер\s*)?(\d{1,2})(?:-?м|-?ым|-?ой|-?е)?\b", t)
    if m:
        return int(m.group(1))

    for word, number in ORDINAL_WORDS.items():
        if word in t:
            return number

    return None


def _safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def _looks_like_edit_request(text: str | None) -> bool:
    t = _clean(text)

    edit_markers = [
        "редакт",
        "измени",
        "изменить",
        "поменяй",
        "замени",
        "смени",
        "вместо",
        "добавь",
        "добавим",
        "убери",
        "удали",
        "выкинь",
        "перенеси",
        "поставь",
        "сделай",
        "в начало",
        "в конец",
        "сюда",
        "в эту тренировку",
        "упражнением",
        "упражнение",
    ]

    workout_context_markers = [
        "тренировк",
        "упражнен",
        "жим",
        "тяга",
        "присед",
        "бицепс",
        "трицепс",
        "пресс",
        "кардио",
        "велосипед",
        "гравитрон",
        "подтяг",
        "развод",
        "мах",
        "гантел",
        "штанг",
        "блок",
    ]

    return any(x in t for x in edit_markers) and any(x in t for x in workout_context_markers)


def fast_parse_workout_edit(text: str | None) -> dict | None:
    """
    Small deterministic shortcut.
    If it does not confidently understand, AI parser will handle.
    """
    t = _clean(text)

    if not _looks_like_edit_request(t):
        return None

    target_date = _parse_target_date(t)

    # add exercise: "добавь восьмым упражнением велосипед", "добавь сюда кардио"
    if any(x in t for x in ["добавь", "добавим", "поставь"]) and any(
        x in t for x in ["упражнен", "сюда", "в эту тренировку", "кардио", "велосипед"]
    ):
        position = _parse_position(t)

        exercise_name = None
        if "велосипед" in t:
            exercise_name = "Велосипед"
        elif "кардио" in t:
            exercise_name = "Кардио"

        if exercise_name:
            return {
                "action": "add_exercise_to_planned_workout",
                "confidence": 0.86,
                "target_date": target_date,
                "exercise_name": exercise_name,
                "exercise_position": position,
                "position_mode": "exact" if position else ("end" if "конец" in t else None),
                "anchor_exercise_name": None,
                "summary": "Добавить упражнение в выбранную плановую тренировку",
            }

    # reorder: "присед в конец", "подтягивания в начало", "сделай жим вторым"
    if any(x in t for x in ["в начало", "в конец", "сделай", "поставь", "перенеси"]):
        position_mode = None
        if "в начало" in t:
            position_mode = "beginning"
        elif "в конец" in t:
            position_mode = "end"

        position = _parse_position(t)

        if position_mode or position:
            return {
                "action": "reorder_exercise",
                "confidence": 0.72,
                "target_date": target_date,
                "exercise_name": None,
                "exercise_position": None,
                "new_position": position,
                "position_mode": position_mode or "exact",
                "anchor_exercise_name": None,
                "summary": "Изменить порядок упражнения в плановой тренировке",
            }

    return None


async def parse_workout_edit_action(text: str, context: dict | None = None) -> dict:
    """
    Parser-first layer for editing an existing planned workout.

    Supported actions:
    - enter_edit_mode
    - add_exercise_to_planned_workout
    - remove_exercise_from_planned_workout
    - replace_exercise
    - reorder_exercise
    - update_exercise_params
    - unknown
    """
    fast = fast_parse_workout_edit(text)
    if fast:
        return fast

    if not _looks_like_edit_request(text):
        return {"action": "unknown", "confidence": 0.0}

    from app.ai import client

    context = context or {}

    system_prompt = f"""
Ты parser редактирования уже существующей плановой тренировки фитнес-бота.

Сегодня: {_today_iso()}

Контекст:
{json.dumps(context, ensure_ascii=False)}

Пользователь говорит свободным языком. Верни structured action JSON.

Формат ответа строго JSON без markdown:

{{
  "action": "enter_edit_mode | add_exercise_to_planned_workout | remove_exercise_from_planned_workout | replace_exercise | reorder_exercise | update_exercise_params | unknown",
  "confidence": 0.0,
  "target_date": null,
  "planned_workout_id": null,
  "exercise_name": null,
  "old_exercise_name": null,
  "new_exercise_name": null,
  "exercise_position": null,
  "new_position": null,
  "position_mode": null,
  "anchor_exercise_name": null,
  "target_sets": null,
  "target_reps_min": null,
  "target_reps_max": null,
  "target_reps_text": null,
  "target_weight_kg": null,
  "preserve_parameters": true,
  "summary": ""
}}

Действия:

1. enter_edit_mode:
- "редактируем тренировку грудных в понедельник"
- "измени тренировку в понедельник"
- "давай отредактируем тренировку на среду"
target_date обязателен, если указан день/дата.

2. add_exercise_to_planned_workout:
- "добавь восьмым упражнением велосипед"
- "добавь сюда кардио"
- "в эту тренировку добавь пресс"
- "после жима добавь разводку"
exercise_name обязателен.
Если сказано "восьмым" => exercise_position=8, position_mode="exact".
"в начало" => position_mode="beginning".
"в конец" => position_mode="end".
"после X" => position_mode="after", anchor_exercise_name=X.
"перед X" => position_mode="before", anchor_exercise_name=X.

3. remove_exercise_from_planned_workout:
- "убери разводку"
- "удали восьмое упражнение"
- "убери последнее упражнение"
Можно указать exercise_name или exercise_position.
"последнее" => position_mode="last".

4. replace_exercise:
- "замени бицепс стоя на бицепс сидя"
- "вместо второго поставь жим штанги"
- "замени шестое упражнение на бицепс сидя"
Можно указать old_exercise_name или exercise_position.
new_exercise_name обязателен.
preserve_parameters=true по умолчанию.

5. reorder_exercise:
- "сделай жим вторым"
- "тягу четвертой"
- "подтягивания в начало"
- "присед в конец"
- "разводку после жима"
Нужно понять exercise_name и куда перенести.
new_position для точного номера.
position_mode: exact | beginning | end | before | after.
anchor_exercise_name для before/after.

6. update_exercise_params:
- "в жиме лежа поставь 95 кг"
- "сделай жим лежа 4 по 10"
- "в гравитроне до отказа"
Нужно указать exercise_name или exercise_position и новые параметры.

Правила:
- "сюда", "в эту тренировку", "в ней" означает использовать selected workout из контекста.
- Если target_date не указан, оставь null: executor использует выбранную/последнюю показанную тренировку.
- "сделай жим вторым" = reorder_exercise.
- "добавь восьмым упражнением велосипед" = add_exercise_to_planned_workout.
- Ответ только JSON.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    parsed = _safe_json_loads(response.choices[0].message.content or "{}")

    if parsed.get("target_date"):
        parsed["target_date"] = _parse_target_date(str(parsed.get("target_date"))) or parsed.get("target_date")

    parsed["confidence"] = float(parsed.get("confidence") or 0)
    return parsed
