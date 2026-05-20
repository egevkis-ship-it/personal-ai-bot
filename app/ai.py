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
- ops — ТОЛЬКО когда явно просят: установить Python-пакет, изменить код самого бота, добавить новый модуль, задеплоить, создать миграцию БД, написать новую функцию. Триггер-слова: "задеплой", "установи pip", "добавь модуль", "напиши код", "сделай миграцию", "положи в репо", "создай PR", "пуш в main", "git commit".
  ⚠️ "удали мои тренировки/сообщения/данные/историю" — это НЕ ops, это fitness (dangerous_delete) или просто команда модуля. НЕ путать!
  ⚠️ "копируй тренировки", "перенеси план" — это fitness, НЕ ops!
  ⚠️ "почисти историю чата" — fitness (или general если не понятно).
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


_FAST_FITNESS_MARKERS = [
    "тренировк", "упражнен", "подход", "повтор",
    "жим", "тяг", "присед", "становая", "бицепс", "трицепс",
    "брус", "подтяг", "отжим", "махи", "разводк", "сгибан", "разгибан",
    "кг", "штанг", "гантел", "rpe", "amrap",
    "что я делал", "что я сделал", "что я записал",
    "вчерашняя тренир", "прошлая тренир", "последняя тренир",
    "архив", "что у меня сегодня", "что сегодня", "что завтра",
    "план на", "тренировка на", "запланируй",
    "закончил трен", "финиш трен", "начинаю трен",
    "вес ", "талия ", "грудь ", "шея ", "бедр", "голен", "живот",
    "замер",
]


def _fast_intent(text: str) -> dict | None:
    """Fast-path: некоторые сообщения можно классифицировать без Haiku.
    Снижает latency на ~1-2 сек для частых фитнес-фраз."""
    t = (text or "").lower().replace("ё", "е").strip()
    if not t or len(t) > 600:
        return None
    if any(m in t for m in _FAST_FITNESS_MARKERS):
        return {"intent": "fitness", "confidence": 0.95, "summary": "fast-path fitness"}
    return None


async def parse_message(text: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")

    # Если текст слишком длинный — почти всегда импорт фитнес-плана. Не гоняем haiku.
    if len(text) > 1500:
        return {"intent": "fitness", "confidence": 0.9, "summary": "long fitness import"}

    # Fast-path: явные фитнес-маркеры → пропускаем Haiku
    fast = _fast_intent(text)
    if fast is not None:
        return fast

    try:
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
    except Exception as e:
        return {"intent": "unknown", "confidence": 0.0, "error": str(e)[:200]}


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
