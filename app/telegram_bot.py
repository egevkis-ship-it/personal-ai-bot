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
        if reply.document_bytes:
            import io
            buf = io.BytesIO(reply.document_bytes)
            buf.name = reply.document_filename or "export.txt"
            await update.message.reply_document(
                document=buf,
                filename=reply.document_filename or "export.txt",
                caption=(reply.document_caption or reply.text or "")[:1024],
            )
            return
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
        "Быстрые команды (фитнес):\n"
        "/today — что сегодня по плану\n"
        "/next — следующая тренировка\n"
        "/week — план недели\n"
        "/last — последняя записанная\n"
        "/finished — закончил тренировку\n"
        "/stats — быстрая сводка\n"
        "/pr — личные рекорды\n"
        "/reminders — мои напоминания\n"
        "/rules — выученные правила\n\n"
        "Примеры свободного текста:\n"
        "— Сделал жим 100кг 3x8\n"
        "— Покажи тренировку в пятницу\n"
        "— Поменяй жим на тягу, 4×10 70кг\n"
        "— Что я сделал на этой неделе\n"
        "— Напомни в 7 утра про тренировку\n"
    )


async def _route_text_as(update: Update, text: str) -> None:
    user_id = _user_id(update)
    reply = await route(user_id, text)
    await _send_reply(update, reply)


async def cmd_today(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "Что сегодня по плану")


async def cmd_next(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "Следующая тренировка")


async def cmd_week(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "План на неделю")


async def cmd_last(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "Последняя тренировка")


async def cmd_finished(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "Закончил тренировку")


async def cmd_stats(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "Быстрая сводка")


async def cmd_pr(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "Мои рекорды")


async def cmd_reminders(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "Мои напоминания")


async def cmd_rules(update, context):
    if not _is_allowed(update): return
    await _route_text_as(update, "Покажи выученные правила")


async def cmd_reset(update, context):
    """Сбросить все висящие pending decisions у юзера."""
    if not _is_allowed(update):
        return
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text
    uid = _user_id(update)
    async with get_session() as s:
        result = await s.execute(
            sql_text("""
                UPDATE fitness_pending_decisions
                SET status = 'cancelled', resolved_at = now()
                WHERE telegram_user_id = :uid AND status = 'pending'
                RETURNING id, decision_type
            """),
            {"uid": uid},
        )
        rows = list(result.mappings().all())
        await s.commit()
    if rows:
        details = "\n".join(f"  #{r['id']} {r['decision_type']}" for r in rows)
        await update.message.reply_text(f"✅ Сбросил {len(rows)} pending decisions:\n{details}")
    else:
        await update.message.reply_text("Активных pending нет.")


async def cmd_run_tests(update, context):
    if not _is_allowed(update):
        return
    await update.message.reply_text("🧪 Запускаю E2E тесты... подожди 30–60с")
    try:
        from app.modules.fitness.e2e_runner import run_scenarios, format_report
        summary = await run_scenarios()
        report = format_report(summary, verbose=False)
        await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f"⚠️ E2E runner упал: {type(e).__name__}: {e}")


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
    import asyncio as _asyncio
    from app.db.migrations.runner import run_migrations
    from app.modules.fitness.reminders import reminder_loop as fitness_reminder_loop
    await run_migrations()
    application.create_task(fitness_reminder_loop(bot=application.bot))

    # Auto-smoke E2E через 90 секунд после старта, если включено
    if os.getenv("RUN_E2E_ON_START", "0") == "1" and settings.allowed_telegram_user_id:
        async def _delayed_smoke():
            await _asyncio.sleep(90)
            try:
                from app.modules.fitness.e2e_runner import run_scenarios, format_report
                summary = await run_scenarios()
                report = format_report(summary, verbose=False)
                await application.bot.send_message(
                    chat_id=int(settings.allowed_telegram_user_id),
                    text="🚀 Авто-E2E после деплоя:\n\n" + report,
                )
            except Exception as e:
                try:
                    await application.bot.send_message(
                        chat_id=int(settings.allowed_telegram_user_id),
                        text=f"⚠️ Авто-E2E упал: {type(e).__name__}: {e}",
                    )
                except Exception:
                    pass
        application.create_task(_delayed_smoke())


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
    # Slash-команды быстрого доступа
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("finished", cmd_finished))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("pr", cmd_pr))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("rules", cmd_rules))
    app.add_handler(CommandHandler("run_tests", cmd_run_tests))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app
