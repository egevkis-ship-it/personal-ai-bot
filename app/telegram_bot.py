import os
import tempfile

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.ai import transcribe_audio
from app.bot_reply import BotReply
from app.config import settings
from app.router import route


def _is_allowed(update: Update) -> bool:
    if not settings.allowed_telegram_user_id:
        return True
    user = update.effective_user
    return user is not None and str(user.id) == str(settings.allowed_telegram_user_id)


def _user_id(update: Update) -> str:
    return str(update.effective_user.id) if update.effective_user else "unknown"


async def _send_reply(update: Update, reply: BotReply | str) -> None:
    if isinstance(reply, BotReply):
        await update.message.reply_text(reply.text, reply_markup=reply.keyboard)
    else:
        await update.message.reply_text(reply)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        f"Привет! Твой Telegram ID: {_user_id(update)}\n\n"
        "Команды:\n"
        "/status — состояние системы\n"
        "/help — список примеров\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    from app.modules.ops.status import build_status_text
    await update.message.reply_text(await build_status_text())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        "Я понимаю свободный текст и голосовые сообщения.\n\n"
        "Примеры:\n"
        "— Сделал жим 100кг 3x8\n"
        "— Потратил 500р на продукты\n"
        "— Напомни завтра в 10 позвонить врачу\n"
        "— Съел 200г куриной грудки\n"
        "— Установи apscheduler\n"
        "— Добавь трекинг сна\n"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return

    user_id = _user_id(update)
    text = update.message.text or ""

    reply = await route(user_id, text)
    await _send_reply(update, reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return

    user_id = _user_id(update)
    audio_path = None

    try:
        voice = update.message.voice
        tg_file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            audio_path = tmp.name

        await tg_file.download_to_drive(audio_path)
        transcript = transcribe_audio(audio_path)

        reply = await route(user_id, transcript)
        if isinstance(reply, BotReply):
            await update.message.reply_text(
                f"Расшифровка:\n{transcript}\n\n{reply.text}",
                reply_markup=reply.keyboard,
            )
        else:
            await update.message.reply_text(f"Расшифровка:\n{transcript}\n\n{reply}")

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not settings.allowed_telegram_user_id:
        pass
    elif str(query.from_user.id) != str(settings.allowed_telegram_user_id):
        return

    user_id = str(query.from_user.id)
    data = query.data

    if data in ("ops_confirm", "ops_cancel"):
        from app.modules.ops.handler import handle_confirm
        confirmed = data == "ops_confirm"
        result = await handle_confirm(user_id, confirmed)
        await query.edit_message_text(result)
        return

    if data.startswith("fit:"):
        from app.modules.fitness.callbacks import handle_fitness_callback
        result = await handle_fitness_callback(user_id, data)
        await query.edit_message_text(result)
        return

    await query.edit_message_text("Неизвестное действие.")


async def _post_init(application: Application) -> None:
    from app.db.migrations.runner import run_migrations
    from app.modules.tasks.reminders import reminder_loop
    await run_migrations()
    application.create_task(reminder_loop())


def build_application() -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app
