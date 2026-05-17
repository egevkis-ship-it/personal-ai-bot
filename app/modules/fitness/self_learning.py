"""
Self-learning module: detects user corrections, extracts rules, injects them
back into parser system prompts as few-shot examples.

Three levels of self-improvement:
  1. Prompt-level (auto, safe): store correction → next parser call sees it.
  2. Preferences (auto, safe): "когда я говорю X, имей в виду Y" → key/value.
  3. Code-level (semi-auto): propose patch via ops module, require confirmation.
"""
from __future__ import annotations
import json
import re

from app.ai import claude_client
from app.db import (
    get_last_interaction,
    save_learning_correction,
    get_active_corrections,
    disable_correction,
    set_user_preference,
)


# Phrases that signal user is correcting the bot
_CORRECTION_TRIGGERS = [
    "ты понял неправильно", "ты понял не так", "понял неправильно", "понял не так",
    "это не так", "не так понял", "неправильно понял", "неправильно разобрал",
    "не то записал", "не то понял", "ты ошибся", "это ошибка",
    "не правильно", "не правельно", "неверно",
    "запомни на будущее", "запомни на будущее", "запомни:",
    "в следующий раз", "впредь", "на будущее",
    "когда я говорю", "если я говорю", "когда я скажу",
    "исправь себя", "почини себя", "научись",
    "ты должен", "ты должна понимать",
    "имей в виду", "учти что", "учти, что",
]

# Phrases that signal user wants to forget a learned rule
_FORGET_TRIGGERS = [
    "забудь правило", "удали правило", "отмени правило",
    "забудь что я говорил", "это правило не нужно",
]


def is_correction_message(text: str) -> bool:
    """True if user message looks like feedback/correction about bot behavior."""
    t = text.lower().replace("ё", "е")
    return any(trigger in t for trigger in _CORRECTION_TRIGGERS)


def is_forget_message(text: str) -> bool:
    t = text.lower().replace("ё", "е")
    return any(trigger in t for trigger in _FORGET_TRIGGERS)


async def extract_correction_rule(
    user_feedback: str,
    last_input: str | None,
    last_response: str | None,
    last_action: str | None,
) -> dict:
    """Ask Claude to extract a structured rule from the user's feedback."""
    system = """Ты экстрактор правил самообучения для AI-бота.

Пользователь дал фидбек, что бот сделал что-то не так. Твоя задача — извлечь правило,
которое бот должен применять В БУДУЩЕМ, чтобы не повторять ошибку.

Контекст:
- last_input: что пользователь написал боту
- last_response: что бот ответил
- last_action: какой экшен бот выбрал
- user_feedback: что пользователь сказал НЕПРАВИЛЬНО

Верни строго JSON:
{
  "correction_type": "parser_error | format | naming | behavior | preference | code_fix",
  "scope": "fitness | general | ops",
  "rule_pattern": "<краткое описание паттерна КОГДА применять, до 200 символов>",
  "rule_action": "<что бот ДОЛЖЕН делать вместо того что сделал, до 300 символов>",
  "preference_key": "<если это preference — короткий ключ, например 'date_format' или 'exercise_alias.жим_штанги'>",
  "preference_value": "<значение, если это preference>",
  "confidence": 0.0,
  "user_facing_summary": "<краткая фраза для ответа пользователю что бот запомнил>"
}

Примеры:
- feedback="когда я говорю 'жим' без уточнения — это жим штанги лёжа"
  → correction_type=preference, preference_key="exercise_alias.жим", preference_value="жим штанги лёжа"

- feedback="ты не должен создавать новую тренировку, если я уже в активной сессии"
  → correction_type=behavior, rule_pattern="active_session есть + log_workout_sets",
    rule_action="append exercises to existing workout, не создавай новую"

- feedback="когда я говорю '4 на 10' — это 4 подхода по 10 повторений"
  → correction_type=parser_error, rule_pattern="'N на M' в контексте подходов",
    rule_action="распознавать как N sets × M reps"

- feedback="показывай даты в формате 18 мая а не 18-05-2026"
  → correction_type=format, preference_key="date_format", preference_value="N месяца"

Только JSON. Без markdown."""

    payload = json.dumps({
        "last_input": last_input,
        "last_response": last_response,
        "last_action": last_action,
        "user_feedback": user_feedback,
    }, ensure_ascii=False)

    response = await claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": payload}],
    )
    raw = response.content[0].text if response.content else "{}"
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end >= 0:
            try:
                return json.loads(raw[start:end + 1])
            except Exception:
                pass
        return {}


async def handle_self_correction(telegram_user_id: str | None, text: str) -> str | None:
    """Process a correction message. Returns response to user."""
    if not is_correction_message(text):
        return None

    last = await get_last_interaction(telegram_user_id)
    if not last:
        return (
            "Понял, что ты хочешь меня поправить, но не помню последнее взаимодействие. "
            "Скажи прямо: «когда я говорю X — это Y» — запомню."
        )

    extracted = await extract_correction_rule(
        user_feedback=text,
        last_input=last.get("input_text"),
        last_response=last.get("bot_response"),
        last_action=last.get("action"),
    )

    if not extracted or extracted.get("confidence", 0) < 0.4:
        return (
            "Я понял что ты меня поправил, но не уверенно извлёк правило. "
            "Сформулируй чётче: «когда я говорю X, делай Y»."
        )

    correction_type = extracted.get("correction_type", "behavior")
    scope = extracted.get("scope", "fitness")
    summary = extracted.get("user_facing_summary") or "Запомнил."

    # If it's a preference — save to user_preferences (faster lookup)
    if correction_type == "preference" and extracted.get("preference_key"):
        await set_user_preference(
            telegram_user_id,
            extracted["preference_key"],
            extracted.get("preference_value", ""),
        )

    # Always save to learning_corrections for parser injection
    cid = await save_learning_correction(
        telegram_user_id=telegram_user_id,
        original_input=last.get("input_text"),
        bot_response=last.get("bot_response"),
        user_feedback=text,
        rule_pattern=extracted.get("rule_pattern", "")[:200],
        rule_action=extracted.get("rule_action", "")[:500],
        correction_type=correction_type,
        scope=scope,
    )

    return f"🧠 {summary}\n\n(правило #{cid}, тип: {correction_type})\n\nПрименю в следующих ответах. Если не сработает — скажи «забудь правило #{cid}»."


async def handle_forget_request(telegram_user_id: str | None, text: str) -> str | None:
    """Disable a previously saved correction."""
    if not is_forget_message(text):
        return None
    m = re.search(r"#?(\d+)", text)
    if not m:
        return "Уточни номер правила: «забудь правило #12»."
    correction_id = int(m.group(1))
    await disable_correction(correction_id)
    return f"🧠 Правило #{correction_id} отключено. Перестану его применять."


async def build_corrections_context(
    telegram_user_id: str | None,
    scope: str = "fitness",
    max_rules: int = 15,
) -> str:
    """Render active corrections as a short block to inject into parser prompts."""
    corrections = await get_active_corrections(telegram_user_id, scope=scope, limit=max_rules)
    if not corrections:
        return ""

    lines = ["═══ Выученные правила (применяй приоритетно) ═══"]
    for c in corrections:
        pattern = c.get("rule_pattern") or ""
        action = c.get("rule_action") or ""
        if pattern and action:
            lines.append(f"- Если: {pattern} → {action}")
    return "\n".join(lines) + "\n"
