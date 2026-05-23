"""
History and export handler.
"""
from __future__ import annotations

import io
import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery

from app.bot.keyboards import export_format, export_period, history_menu
from app.bot.services.formatter import format_csv, format_history_list, format_workout_summary
from app.bot.states import HistoryStates
from app.db import get_workout_sets, get_workouts_range

log = logging.getLogger(__name__)
router = Router(name="history")


async def _load_workouts_with_sets(uid: str, from_date: date, to_date: date):
    workouts = await get_workouts_range(uid, from_date, to_date)
    result = []
    for w in workouts:
        sets = await get_workout_sets(w["id"])
        result.append((w, sets))
    return result


# ─────────────────────────── period shortcuts ────────────────────────────────

@router.callback_query(F.data == "hist:today")
async def cb_hist_today(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    uid = str(cb.from_user.id)
    today = date.today()
    data = await _load_workouts_with_sets(uid, today, today)
    text = format_history_list(data)
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=history_menu())


@router.callback_query(F.data == "hist:week")
async def cb_hist_week(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    uid = str(cb.from_user.id)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    data = await _load_workouts_with_sets(uid, week_start, today)
    text = format_history_list(data)
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=history_menu())


@router.callback_query(F.data == "hist:month")
async def cb_hist_month(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    uid = str(cb.from_user.id)
    today = date.today()
    month_start = today.replace(day=1)
    data = await _load_workouts_with_sets(uid, month_start, today)
    text = format_history_list(data)
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=history_menu())


# ─────────────────────────── export ──────────────────────────────────────────

@router.callback_query(F.data == "hist:export")
async def cb_export_start(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(HistoryStates.choose_export)
    await cb.message.edit_text("📤 Выбери формат экспорта:", reply_markup=export_format())


@router.callback_query(F.data.startswith("export:"), HistoryStates.choose_export)
async def cb_export_format(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    fmt = cb.data.split(":")[1]  # "text" or "csv"
    await state.update_data(export_fmt=fmt)
    await cb.message.edit_text("📆 За какой период?", reply_markup=export_period())


@router.callback_query(F.data.startswith("export_period:"))
async def cb_export_period(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    period = cb.data.split(":")[1]  # "7", "30", "all"
    data = await state.get_data()
    fmt = data.get("export_fmt", "text")
    uid = str(cb.from_user.id)
    today = date.today()

    if period == "all":
        from_date = date(2020, 1, 1)
    else:
        from_date = today - timedelta(days=int(period))

    workouts_with_sets = await _load_workouts_with_sets(uid, from_date, today)

    await state.clear()

    if not workouts_with_sets:
        await cb.message.edit_text("📭 Нет данных за выбранный период.", reply_markup=history_menu())
        return

    if fmt == "csv":
        csv_content = format_csv(workouts_with_sets)
        filename = f"workout_export_{today.isoformat()}.csv"
        file = BufferedInputFile(csv_content.encode("utf-8"), filename=filename)
        await cb.message.answer_document(
            document=file,
            caption=f"📊 Экспорт тренировок за {period if period != 'all' else 'всё время'}",
        )
    else:
        text = format_history_list(workouts_with_sets)
        # Telegram limit is 4096 chars; split if needed
        if len(text) <= 4000:
            await cb.message.answer(text, parse_mode="HTML")
        else:
            chunks = []
            current = ""
            for line in text.split("\n"):
                if len(current) + len(line) + 1 > 4000:
                    chunks.append(current)
                    current = line
                else:
                    current += ("\n" if current else "") + line
            if current:
                chunks.append(current)
            for chunk in chunks:
                await cb.message.answer(chunk, parse_mode="HTML")

    await cb.message.answer("📖 История:", reply_markup=history_menu())
