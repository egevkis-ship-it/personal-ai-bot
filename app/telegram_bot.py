import json
import os
import tempfile

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from app.config import settings
from app.ai import parse_message, transcribe_audio, generate_general_answer
from app.db import save_raw_message
from app.modules.ops.status import build_status_text


def is_allowed(update: Update) -> bool:
    if not settings.allowed_telegram_user_id:
        return True

    user = update.effective_user
    if not user:
        return False

    return str(user.id) == str(settings.allowed_telegram_user_id)


def build_reply(parsed: dict, source_text: str | None = None) -> str:
    intent = parsed.get("intent", "unknown")
    confidence = parsed.get("confidence")
    summary = parsed.get("summary") or "Принял."

    if intent == "general_question" and source_text:
        return generate_general_answer(source_text)

    if parsed.get("requires_confirmation"):
        return (
            f"Я понял задачу так:\n\n"
            f"Тип: {intent}\n"
            f"Уверенность: {confidence}\n"
            f"Сводка: {summary}\n\n"
            f"Пока я только логирую такие действия. "
            f"На следующем этапе добавим подтверждение кнопками."
        )

    return (
        f"Записал.\n\n"
        f"Тип: {intent}\n"
        f"Сводка: {summary}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    user_id = update.effective_user.id if update.effective_user else "unknown"

    await update.message.reply_text(
        "Personal AI Bot запущен.\n"
        f"Твой Telegram user_id: {user_id}\n\n"
        "Команды:\n"
        "/status — статус системы\n"
        "/start — показать это сообщение"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    await update.message.reply_text(await build_status_text())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    text = update.message.text
    user_id = str(update.effective_user.id) if update.effective_user else None

    await update.message.reply_text("Принял. Разбираю...")

    try:
        parsed = parse_message(text)
        parsed_json = json.dumps(parsed, ensure_ascii=False)

        await save_raw_message(
            telegram_user_id=user_id,
            message_type="text",
            original_text=text,
            transcript=None,
            intent=parsed.get("intent"),
            parsed_json=parsed_json,
            status="parsed",
        )

        await update.message.reply_text(build_reply(parsed, text))

    except Exception as e:
        await save_raw_message(
            telegram_user_id=user_id,
            message_type="text",
            original_text=text,
            transcript=None,
            intent="error",
            parsed_json=None,
            status="error",
            error=str(e),
        )
        await update.message.reply_text(f"Ошибка обработки: {e}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    user_id = str(update.effective_user.id) if update.effective_user else None

    await update.message.reply_text("Принял голосовое. Расшифровываю...")

    audio_path = None

    try:
        voice = update.message.voice
        tg_file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            audio_path = tmp.name

        await tg_file.download_to_drive(audio_path)

        transcript = transcribe_audio(audio_path)
        parsed = parse_message(transcript)
        parsed_json = json.dumps(parsed, ensure_ascii=False)

        await save_raw_message(
            telegram_user_id=user_id,
            message_type="voice",
            original_text=None,
            transcript=transcript,
            intent=parsed.get("intent"),
            parsed_json=parsed_json,
            status="parsed",
        )

        await update.message.reply_text(
            f"Расшифровка:\n{transcript}\n\n{build_reply(parsed, transcript)}"
        )

    except Exception as e:
        await save_raw_message(
            telegram_user_id=user_id,
            message_type="voice",
            original_text=None,
            transcript=None,
            intent="error",
            parsed_json=None,
            status="error",
            error=str(e),
        )
        await update.message.reply_text(f"Ошибка обработки голосового: {e}")

    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)


def build_application() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app
