from __future__ import annotations
import json
from datetime import date

from app.ai import claude_client


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


async def parse_pending_decision_response(text: str, context: dict) -> dict:
    today = date.today().isoformat()

    system_prompt = f"""
Ты parser ответа на pending decision фитнес-ассистента Егора.

Сегодня: {today}

Есть незакрытый конфликт тренировочного плана:
{json.dumps(context, ensure_ascii=False)}

Пользователь ответил новым сообщением. Определи, относится ли оно к конфликту.

Верни строго JSON:
{{
  "relates_to_pending": true,
  "action": "keep_both | move_workout | move_to_nearest_free_day | show_week_plan | cancel_pending | unknown",
  "target": {{
    "planned_workout_id": null,
    "focus": null,
    "focus_label": null
  }},
  "new_date": null,
  "new_weekday": null,
  "summary": ""
}}

Правила:
- "оставь обе", "пусть будут обе", "оставь так" = keep_both.
- "ничего не делай", "забей", "отмена" = cancel_pending.
- "покажи план", "план недели" = show_week_plan.
- "перенеси грудь на воскресенье" = move_workout.
- "плечи на понедельник" = move_workout.
- "перенеси грудь на ближайший свободный день" = move_to_nearest_free_day.
- Если фраза явно про одну из конфликтных тренировок — relates_to_pending=true.
- Если фраза вообще новая команда и не относится к конфликту — relates_to_pending=false.
- Если дата названа днём недели, вычисли ближайшую дату этого дня.
- Ответ только JSON.
"""

    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )

    return safe_json_loads(response.content[0].text if response.content else "{}")
