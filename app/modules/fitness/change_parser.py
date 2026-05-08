import json
from datetime import date

from app.ai import client
from app.modules.fitness.utils import week_bounds


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def parse_plan_change(text: str) -> dict:
    today = date.today().isoformat()
    week_start, week_end = week_bounds()

    system_prompt = f"""
Ты parser изменений тренировочного плана Егора.

Сегодня: {today}
Текущая неделя: {week_start} — {week_end}

Верни строго JSON.

Формат:
{{
  "change_type": "skip | move | swap | replace | custom_today | unknown",
  "target": {{
    "focus": null,
    "focus_label": null,
    "date": null,
    "sequence_number": null,
    "is_today": false,
    "is_next": false
  }},
  "second_target": {{
    "focus": null,
    "focus_label": null,
    "date": null,
    "sequence_number": null
  }},
  "new_date": null,
  "new_weekday": null,
  "reason": null,
  "replacement": {{
    "title": null,
    "focus": null,
    "focus_label": null,
    "notes": null,
    "exercises": [
      {{
        "exercise_order": 1,
        "exercise_name": null,
        "target_sets": null,
        "target_reps_min": null,
        "target_reps_max": null,
        "target_reps_text": null,
        "target_weight_kg": null,
        "notes": null
      }}
    ]
  }},
  "summary": ""
}}

Правила:
- "пропустил", "пропускаем", "не буду тренироваться" = skip.
- "перенеси плечи на пятницу" = move, target focus shoulders, new_date ближайшая пятница.
- "перенеси следующую тренировку на завтра" = move, target.is_next=true.
- "поменяй местами грудь и плечи" = swap.
- "вместо ног поставь грудь" = replace, target legs, replacement chest.
- "ноги пропускаем, болит колено, вместо ног грудь" = replace, reason "болит колено".
- "сегодня сделаю кастомную тренировку" = custom_today.
- Фокусы нормализуй:
  грудь=chest, спина=back, плечи=shoulders, ноги=legs, руки=arms, пресс=abs.
- Если дата не указана, оставь null.
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

    return safe_json_loads(response.choices[0].message.content or "{}")
