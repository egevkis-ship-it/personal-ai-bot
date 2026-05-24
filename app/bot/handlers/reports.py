"""
Period report handler. Generates a PDF with workouts, measurements and photos
for a chosen period and sends it as a Telegram document.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.bot.keyboards import main_menu, reports_menu
from app.bot.services.report_builder import build_period_report
from app.bot.states import ReportStates

log = logging.getLogger(__name__)
router = Router(name="reports")


@router.message(F.text == "📋 Отчёты")
async def menu_reports(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "📋 <b>Отчёты</b>\n\n"
        "Сгенерирую PDF за выбранный период:\n"
        "  • Сводка по тренировкам, тоннажу, подходам\n"
        "  • Все тренировки списком с комментариями\n"
        "  • Таблица замеров с дельтой за период\n"
        "  • Фото с фитнес-анализом и миниатюрами",
        parse_mode="HTML",
        reply_markup=reports_menu(),
    )


@router.callback_query(F.data.startswith("rep:") & ~F.data.endswith("custom"))
async def cb_report_preset(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    days = int(cb.data.split(":")[1])
    today = date.today()
    from_date = today - timedelta(days=days)
    await _generate_and_send(cb.message, cb.from_user.id, cb.bot, from_date, today)


@router.callback_query(F.data == "rep:custom")
async def cb_report_custom(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(ReportStates.waiting_custom_range)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "✍️ Отправь интервал одной строкой.\n\n"
        "Форматы:\n"
        "  <i>2026-04-01 2026-05-24</i>\n"
        "  <i>01.04.2026 — 24.05.2026</i>\n"
        "  <i>01.04 — 24.05</i>  (года = текущий)\n\n"
        "Или нажми /start чтобы отменить.",
        parse_mode="HTML",
    )


_DATE_RE = re.compile(r"(\d{1,4}[.\-/]\d{1,2}(?:[.\-/]\d{1,4})?)")


def _parse_date(token: str) -> date | None:
    t = token.strip().replace("/", ".").replace("-", ".")
    parts = t.split(".")
    if len(parts) == 3:
        # could be DD.MM.YYYY or YYYY.MM.DD
        try:
            a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            return None
        if a > 31:
            # YYYY.MM.DD
            try:
                return date(a, b, c)
            except ValueError:
                return None
        # DD.MM.YYYY
        try:
            return date(c if c > 99 else 2000 + c, b, a)
        except ValueError:
            return None
    if len(parts) == 2:
        try:
            d, m = int(parts[0]), int(parts[1])
            return date(date.today().year, m, d)
        except ValueError:
            return None
    return None


@router.message(ReportStates.waiting_custom_range, F.text)
async def handle_custom_range(message: Message, state: FSMContext) -> None:
    tokens = _DATE_RE.findall(message.text or "")
    if len(tokens) < 2:
        await message.answer(
            "❌ Не нашёл две даты. Отправь, например: <i>01.04.2026 24.05.2026</i>",
            parse_mode="HTML",
        )
        return
    d1 = _parse_date(tokens[0])
    d2 = _parse_date(tokens[1])
    if not d1 or not d2:
        await message.answer("❌ Не получилось распарсить даты. Попробуй формат DD.MM.YYYY.")
        return
    if d1 > d2:
        d1, d2 = d2, d1
    await state.clear()
    await _generate_and_send(message, message.from_user.id, message.bot, d1, d2)


async def _generate_and_send(message: Message, user_id: int, bot, from_date: date, to_date: date) -> None:
    uid = str(user_id)
    placeholder = await message.answer(
        f"🧠 Готовлю отчёт за {from_date} — {to_date}…\n"
        "Скачиваю фото, формирую PDF. 10-40 секунд."
    )
    try:
        pdf_bytes = await build_period_report(bot, uid, from_date, to_date)
    except Exception as exc:
        log.exception("report build failed")
        import html as _html
        try:
            await placeholder.edit_text(
                f"❌ Не получилось собрать отчёт: <code>{_html.escape(str(exc))[:300]}</code>",
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(f"❌ Ошибка: {exc}")
        await message.answer("Главное меню:", reply_markup=main_menu())
        return

    filename = f"report_{from_date.isoformat()}_{to_date.isoformat()}.pdf"
    doc = BufferedInputFile(pdf_bytes, filename=filename)
    try:
        await placeholder.edit_text(f"✅ Готово. Отправляю PDF ({len(pdf_bytes)/1024:.0f} КБ)…")
    except Exception:
        pass
    try:
        await message.answer_document(
            document=doc,
            caption=f"📋 Отчёт {from_date} — {to_date}",
        )
    except Exception as exc:
        log.exception("send document failed")
        await message.answer(f"❌ Не удалось отправить PDF: {exc}")
        return
    await message.answer("Готово.", reply_markup=reports_menu())
