import logging
import re

from app.ai import parse_message
from app.bot_reply import BotReply
from app.state.manager import SessionType, get_session

logger = logging.getLogger(__name__)

_FITNESS_INTENTS = {"fitness"}
_FINANCE_INTENTS = {"finance_expense", "finance_income"}
_TASK_INTENTS = {"task", "reminder"}
_NUTRITION_INTENTS = {"nutrition"}
_OPS_INTENTS = {"ops"}


# Сильные fitness-сигналы — если в тексте есть, ВСЕГДА fitness (минуя intent classifier).
_HARD_FITNESS_PATTERNS = [
    r"\bтренировк",
    r"\bупражнен",
    r"\bподход",
    r"\bжим\b", r"\bтяг", r"\bприсед", r"\bстановая",
    r"\bбицепс", r"\bтрицепс", r"\bдельт", r"\bягодиц",
    r"\d+\s*кг\b",
    r"\d+\s*[×x]\s*\d+",
    r"\bштанг", r"\bгантел",
    r"\bвес[ам]", r"\bРПЕ\b", r"\bRPE\b", r"\bAMRAP\b",
    r"\bразминк", r"\bрабочих", r"\bрабочие\s+подход",
    r"\bплан на (неделю|месяц|следующ)",
    r"\bтрениров", r"\bзапланир", r"\bзапиши.*трен",
]


def _has_hard_fitness_signal(text: str) -> bool:
    t = (text or "").lower().replace("ё", "е")
    hits = sum(1 for p in _HARD_FITNESS_PATTERNS if re.search(p, t))
    return hits >= 2  # минимум 2 сигнала чтобы исключить случайности


# Жёсткие ops-сигналы. БЕЗ них — ops НЕ запускается, даже если intent=ops.
_HARD_OPS_PATTERNS = [
    r"\bзадеплой", r"\bдеплой\b", r"\bdeploy\b",
    r"\bустанови.*(pip|пакет|библиотек|зависимост)",
    r"\bpip install\b", r"\buv add\b", r"\bpoetry add\b",
    r"\bнапиши код", r"\bдобавь модуль", r"\bдобавь файл",
    r"\bсделай (миграцию|migration)", r"\bcommit\b", r"\bgit (commit|push|pull)",
    r"\bpush в (main|master|origin)", r"\bпуш в (main|master|origin)",
    r"\bcreate pr\b", r"\bсоздай pr\b",
    r"\bнапиши функцию", r"\bдобавь функцию.*в код",
    r"\bкласс\s+[A-Z]", r"\bимпорт\b",
    r"\bпропиши\b.*\b(в код|функцию|роут|hand)",
    r"\bв коде\b", r"\bв файле\b.*\.py",
]


def _has_hard_ops_signal(text: str) -> bool:
    t = (text or "").lower().replace("ё", "е")
    return any(re.search(p, t) for p in _HARD_OPS_PATTERNS)


async def route(user_id: str, text: str) -> BotReply | str:
    try:
        # 1. Active workout session — route directly without AI parsing
        workout_session = await get_session(user_id, SessionType.WORKOUT)
        if workout_session is not None:
            from app.modules.fitness.session import handle_session_message
            return await handle_session_message(user_id, text, workout_session)

        # 2. Awaiting user input
        pending = await get_session(user_id, SessionType.AWAITING_INPUT)
        if pending is not None:
            return await _dispatch_pending(user_id, text, pending)

        # 3. Pending confirmation
        confirm = await get_session(user_id, SessionType.PENDING_CONFIRM)
        if confirm is not None:
            return await _dispatch_confirm(user_id, text, confirm)

        # 4. HARD fitness signal short-circuit — длинный план или явные веса/упражнения
        #    идут в фитнес сразу, минуя haiku-классификатор (он часто ошибается на длинных текстах)
        if _has_hard_fitness_signal(text):
            logger.debug(f"user={user_id} hard fitness signal — direct route")
            from app.modules.fitness.handler import handle
            return await handle(user_id, text, {"intent": "fitness", "confidence": 1.0})

        # 5. Parse intent with Claude (safe)
        try:
            parsed = await parse_message(text)
        except Exception as e:
            logger.exception("parse_message failed: %s", e)
            parsed = {"intent": "unknown", "confidence": 0.0, "error": str(e)[:200]}
        intent = parsed.get("intent", "unknown")
        logger.debug(f"user={user_id} intent={intent} confidence={parsed.get('confidence')}")

        return await _dispatch_intent(user_id, text, intent, parsed)
    except Exception as e:
        logger.exception("route() crashed: %s", e)
        return (
            f"⚠️ Внутренняя ошибка при обработке сообщения: {type(e).__name__}: {str(e)[:200]}\n\n"
            "Попробуй переформулировать или разбить на меньшие сообщения."
        )


async def _dispatch_intent(user_id: str, text: str, intent: str, parsed: dict) -> str:
    if intent in _FITNESS_INTENTS:
        from app.modules.fitness.handler import handle
        return await handle(user_id, text, parsed)

    if intent in _FINANCE_INTENTS:
        from app.modules.finance.handler import handle
        return await handle(user_id, text, parsed)

    if intent in _TASK_INTENTS:
        from app.modules.tasks.handler import handle
        return await handle(user_id, text, parsed)

    if intent in _NUTRITION_INTENTS:
        from app.modules.nutrition.handler import handle
        return await handle(user_id, text, parsed)

    if intent in _OPS_INTENTS:
        # Двойная защита: classifier мог ошибочно поставить ops на
        # "удали данные / копируй тренировки / почисти историю" — это НЕ ops.
        # ops запускаем только при явных code/deploy маркерах.
        if not _has_hard_ops_signal(text):
            logger.warning(f"intent=ops but no hard ops signal in: {text[:120]!r}")
            if _has_hard_fitness_signal(text):
                from app.modules.fitness.handler import handle
                return await handle(user_id, text, parsed)
            from app.ai import generate_general_answer
            return await generate_general_answer(text)
        from app.modules.ops.handler import handle
        return await handle(user_id, text, parsed)

    if intent == "general_question":
        from app.ai import generate_general_answer
        return await generate_general_answer(text)

    return f"Понял: {parsed.get('summary') or text}"


async def _dispatch_pending(user_id: str, text: str, pending: dict) -> str:
    module = pending.get("module")
    if module == "fitness":
        from app.modules.fitness.handler import handle_pending
        return await handle_pending(user_id, text, pending)
    if module == "finance":
        from app.modules.finance.handler import handle_pending
        return await handle_pending(user_id, text, pending)
    if module == "tasks":
        from app.modules.tasks.handler import handle_pending
        return await handle_pending(user_id, text, pending)
    if module == "ops":
        from app.modules.ops.handler import handle_pending
        return await handle_pending(user_id, text, pending)
    return "Не понял ответ."


async def _dispatch_confirm(user_id: str, text: str, confirm: dict) -> str:
    normalized = text.strip().lower()
    confirmed = any(w in normalized for w in ["да", "подтверждаю", "ок", "yes", "y"])
    cancelled = any(w in normalized for w in ["нет", "отмена", "отменяю", "no", "n"])

    if not confirmed and not cancelled:
        return "Подтверди или отмени действие."

    module = confirm.get("module")
    if module == "ops":
        from app.modules.ops.handler import handle_confirm
        return await handle_confirm(user_id, confirmed, confirm)

    return "Действие отменено." if cancelled else "Действие подтверждено."
