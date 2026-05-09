from __future__ import annotations
import json

from app.ai import client


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def parse_create_plan_conflict_response(text: str, context: dict) -> dict:
    """
    Parses a user's free-form response to a pending create_plan_conflict decision.

    Returns:
    {
      "relates_to_pending": true/false,
      "action": "replace_existing_plan | append_to_existing_plan | cancel | show_existing_plan | unclear",
      "confidence": 0.0-1.0,
      "summary": "..."
    }
    """

    system_prompt = f"""
Ты parser ответа на pending decision фитнес-ассистента.

Есть незакрытое решение: пользователь попытался создать новый тренировочный план на период,
в котором уже есть активные тренировки. Бот спросил, что сделать:
- заменить старый план новым
- добавить новый план к существующему
- отменить действие

Контекст pending decision:
{json.dumps(context, ensure_ascii=False)}

Пользователь ответил:
{text}

Твоя задача — понять смысл ответа, а не искать точные фразы.

Верни строго JSON:
{{
  "relates_to_pending": true,
  "action": "replace_existing_plan | append_to_existing_plan | cancel | show_existing_plan | unclear",
  "confidence": 0.0,
  "summary": ""
}}

Правила:

1. replace_existing_plan:
Если пользователь хочет заменить/обновить/поменять/сменить/перезаписать старый план новым.
Примеры:
- замени
- меняй
- поменяй
- смени
- обнови план
- да, ставь новый
- старый убери, новый поставь
- почисти старый и поставь этот
- новый вместо старого
- давай заменим
- ок, делай новый
- пересобери неделю
- замени старый план
- вместо старого поставь новый

2. append_to_existing_plan:
Если пользователь хочет добавить новый план/тренировки к существующим, не удаляя старые.
Примеры:
- добавь
- добавь сверху
- добавь к существующему
- пусть будут оба
- оставь старый и добавь новый
- добавь дополнительно
- докинь к текущему

3. cancel:
Если пользователь хочет отменить действие и ничего не менять.
Примеры:
- отмена
- отмени
- не надо
- ничего не делай
- забей
- оставь как было
- стоп
- не меняй
- пока не надо

4. show_existing_plan:
Если пользователь просит сначала показать текущий/старый/существующий план.
Примеры:
- покажи старый план
- покажи что сейчас
- покажи текущий план
- что уже стоит

5. relates_to_pending=false:
Если сообщение явно не относится к этому решению, например новый независимый запрос.

6. unclear:
Если сообщение относится к pending decision, но непонятно, заменить, добавить или отменить.

Ответ только JSON. Без markdown.
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
