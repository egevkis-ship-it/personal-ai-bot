import json
from datetime import datetime

from openai import OpenAI

from app.config import settings


client = OpenAI(api_key=settings.openai_api_key)


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def parse_message(text: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")

    system_prompt = f"""
Ты персональный AI-ассистент Егора в Telegram.

Твоя задача — определить intent сообщения и вернуть строго JSON.

Поддерживаемые intent:
- fitness_workout
- body_measurement
- nutrition
- translation
- task
- reminder
- construction_note
- finance_expense
- ops
- general_question
- unknown

Сегодняшняя дата: {today}

Формат ответа:
{{
  "intent": "...",
  "confidence": 0.0,
  "date": "YYYY-MM-DD",
  "requires_confirmation": false,
  "summary": "...",
  "data": {{}}
}}

Правила:
- Ответ только JSON, без markdown.
- Если дата не указана, используй сегодняшнюю.
- Если это расход, задача, ops-действие или изменение данных — requires_confirmation=true.
- Если это фитнес, замеры или питание — можно requires_confirmation=false.
- Не выдумывай точные калории, если пользователь явно не просит посчитать.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    content = response.choices[0].message.content or "{}"
    return safe_json_loads(content)


def transcribe_audio(file_path: str) -> str:
    with open(file_path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_file,
            language="ru",
        )
    return result.text


def generate_general_answer(text: str) -> str:
    system_prompt = """
Ты персональный Telegram AI-ассистент Егора.

Отвечай естественно, кратко и по делу.
Ты уже умеешь принимать текст и голосовые, расшифровывать их, определять тип задачи и сохранять события в базу.
Не говори, что ты полноценный готовый ассистент на все случаи жизни — система ещё развивается.
Если пользователь просто общается, отвечай нормально как ассистент.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    return response.choices[0].message.content or "Понял."


def generate_general_answer(text: str) -> str:
    system_prompt = """
Ты персональный Telegram AI-ассистент Егора.

Отвечай естественно, кратко и по делу.
Ты уже умеешь принимать текст и голосовые, расшифровывать их, определять тип задачи и сохранять события в базу.
Не говори, что ты полноценный готовый ассистент на все случаи жизни — система ещё развивается.
Если пользователь просто общается, отвечай нормально как ассистент.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    return response.choices[0].message.content or "Понял."
