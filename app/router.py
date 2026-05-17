import logging

from app.ai import parse_message
from app.state.manager import SessionType, get_session

logger = logging.getLogger(__name__)

# Imported lazily to avoid circular imports at module level
_FITNESS_INTENTS = {"fitness"}
_FINANCE_INTENTS = {"finance_expense", "finance_income"}
_TASK_INTENTS = {"task", "reminder"}
_NUTRITION_INTENTS = {"nutrition"}
_OPS_INTENTS = {"ops"}


async def route(user_id: str, text: str) -> str:
    # 1. Active workout session — route directly without AI parsing
    workout_session = await get_session(user_id, SessionType.WORKOUT)
    if workout_session is not None:
        from app.modules.fitness.session import handle_session_message
        return await handle_session_message(user_id, text, workout_session)

    # 2. Awaiting user input (e.g. confirmation, disambiguation)
    pending = await get_session(user_id, SessionType.AWAITING_INPUT)
    if pending is not None:
        return await _dispatch_pending(user_id, text, pending)

    # 3. Pending confirmation (destructive ops)
    confirm = await get_session(user_id, SessionType.PENDING_CONFIRM)
    if confirm is not None:
        return await _dispatch_confirm(user_id, text, confirm)

    # 4. Parse intent with Claude
    parsed = await parse_message(text)
    intent = parsed.get("intent", "unknown")
    logger.debug(f"user={user_id} intent={intent} confidence={parsed.get('confidence')}")

    return await _dispatch_intent(user_id, text, intent, parsed)


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
