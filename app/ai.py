import json
from datetime import datetime

from anthropic import AsyncAnthropic
from openai import OpenAI

from app.config import settings


# OpenAI client — kept for audio transcription and legacy fitness module imports
client = OpenAI(api_key=settings.openai_api_key)

# Anthropic client — used for all text parsing and generation
claude_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

_PARSE_SYSTEM_PROMPT = """\
Ты персональный AI-ассистент Егора в Telegram.

Твоя задача — определить intent сообщения и вернуть строго JSON.

Поддерживаемые intent:
- fitness — тренировки, упражнения, вес, замеры тела, фитнес-план
- nutrition — еда, калории, приёмы пищи, БЖУ
- translation — перевод текста
- task — задачи, проекты, дедлайны
- reminder — напоминания
- construction_note — стройка, ремонт, материалы
- finance_expense — расходы, доходы, финансы
- ops — системные команды: установить пакет, добавить модуль, изменить код, создать таблицу в БД, задеплоить
- general_question — общий вопрос или разговор
- unknown — непонятное

Формат ответа:
{
  "intent": "...",
  "confidence": 0.0,
  "date": "YYYY-MM-DD",
  "requires_confirmation": false,
  "summary": "...",
  "data": {}
}

Правила:
- Ответ только JSON, без markdown.
- Если дата не указана, используй сегодняшнюю.
- intent="fitness" ТОЛЬКО если сообщение явно про: тренировки, упражнения, подходы/повторы, веса/штанги/гантели, план/программу, прогресс/рекорды, замеры тела (вес, талия, % жира), активную сессию.
- Команды показа (что я сделал/что у меня по плану/последняя тренировка/прогресс/рекорды) — тоже fitness.
- Если человек просто здоровается, спрашивает "сколько времени", "какая погода", "что такое X", переводит, шутит — это general_question (или translation). НЕ fitness.
- Если это расход, задача, ops-действие или изменение данных — requires_confirmation=true.
- Если это фитнес, замеры или питание — можно requires_confirmation=false.
- Не выдумывай точные калории, если пользователь явно не просит посчитать.\
"""

_GENERAL_SYSTEM_PROMPT = """\
Ты персональный Telegram AI-ассистент Егора.

Отвечай естественно, кратко и по делу.
Ты уже умеешь принимать текст и голосовые, расшифровывать их, определять тип задачи и сохранять события в базу.
Не говори, что ты полноценный готовый ассистент на все случаи жизни — система ещё развивается.
Если пользователь просто общается, отвечай нормально как ассистент.\
"""


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


async def parse_message(text: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")

    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        system=[
            {
                "type": "text",
                "text": _PARSE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": f"Сегодняшняя дата: {today}",
            },
        ],
        messages=[{"role": "user", "content": text}],
    )

    content = response.content[0].text if response.content else "{}"
    return safe_json_loads(content)


async def generate_general_answer(text: str) -> str:
    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": _GENERAL_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": text}],
    )

    return response.content[0].text if response.content else "Понял."


def transcribe_audio(file_path: str) -> str:
    with open(file_path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_file,
            language="ru",
        )
    return result.text
