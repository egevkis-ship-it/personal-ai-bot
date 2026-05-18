import logging
import traceback

from app.modules.fitness.action_v2 import handle_fitness_action_v2

logger = logging.getLogger(__name__)


async def _safe_handle(user_id: str, text: str, fallback: str) -> str:
    """Wrap handle_fitness_action_v2 with guaranteed response — никогда не молчим."""
    try:
        result = await handle_fitness_action_v2(user_id, text)
    except Exception as e:  # noqa: BLE001
        logger.exception("fitness handler crashed: %s", e)
        return (
            f"⚠️ Я попытался обработать запрос, но упал с ошибкой.\n"
            f"Подробности: {type(e).__name__}: {str(e)[:200]}\n\n"
            f"Скажи проще или другими словами — попробую ещё раз."
        )
    return result or fallback


async def handle(user_id: str, text: str, parsed: dict) -> str:
    return await _safe_handle(user_id, text, "Не понял, уточни запрос. Скажи проще или с примером.")


async def handle_pending(user_id: str, text: str, pending: dict) -> str:
    return await _safe_handle(user_id, text, "Не понял ответ. Скажи «да/нет» или уточни.")
