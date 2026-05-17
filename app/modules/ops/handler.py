import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot_reply import BotReply
from app.modules.ops.agent import run_agent
from app.modules.ops.coolify import trigger_deploy
from app.modules.ops.github_client import commit_files
from app.state.manager import SessionType, clear_session, get_session, set_session

logger = logging.getLogger(__name__)


async def handle(user_id: str, text: str, parsed: dict) -> BotReply:
    result = await run_agent(text)

    if not result.pending_files:
        return BotReply(text=result.message)

    await set_session(user_id, SessionType.PENDING_CONFIRM, {
        "module": "ops",
        "pending_files": result.pending_files,
        "commit_message": result.commit_message,
    })

    files_list = "\n".join(f"  • {p}" for p in result.pending_files)
    confirmation_text = (
        f"{result.message}\n\n"
        f"Файлы для изменения:\n{files_list}\n\n"
        f"Применить и задеплоить?"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, применить", callback_data="ops_confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="ops_cancel"),
    ]])

    return BotReply(text=confirmation_text, keyboard=keyboard)


async def handle_confirm(user_id: str, confirmed: bool, confirm: dict = None) -> str:
    pending = await get_session(user_id, SessionType.PENDING_CONFIRM)
    await clear_session(user_id, SessionType.PENDING_CONFIRM)

    if not confirmed:
        return "Отменено. Изменения не применены."

    if not pending or pending.get("module") != "ops":
        return "Нечего применять."

    pending_files: dict[str, str] = pending["pending_files"]
    commit_message: str = pending.get("commit_message", "bot: apply changes")

    try:
        sha = await commit_files(pending_files, commit_message)
        deploy_status = await trigger_deploy()
        files_list = "\n".join(f"  • {p}" for p in pending_files)
        return (
            f"Готово. Коммит: {sha[:7]}\n\n"
            f"Изменённые файлы:\n{files_list}\n\n"
            f"{deploy_status}"
        )
    except Exception as e:
        logger.exception("Failed to commit/deploy")
        return f"Ошибка при деплое: {e}"


async def handle_pending(user_id: str, text: str, pending: dict) -> str:
    return "Используй кнопки для подтверждения."
