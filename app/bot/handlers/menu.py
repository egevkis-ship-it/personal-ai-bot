"""
Main menu and /start handler.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import back_button, history_menu, main_menu, plan_menu, workout_start
from app.config import settings
from app.db import get_active_workout, get_today_plan

router = Router(name="menu")

# ─────────────────────── access guard ────────────────────────────────────────

async def _check_access(message: Message) -> bool:
    """Return False and reply if user is not allowed."""
    allowed = settings.allowed_telegram_user_id
    if allowed and str(message.from_user.id) != allowed:
        await message.answer("⛔ Доступ запрещён.")
        return False
    return True


# ─────────────────────── /start ──────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not await _check_access(message):
        return
    await state.clear()
    name = message.from_user.first_name or "Атлет"
    await message.answer(
        f"Привет, {name}! 💪\n\nЯ помогу записывать тренировки, вести планы и смотреть историю.",
        reply_markup=main_menu(),
    )


# ─────────────────────── main menu buttons ───────────────────────────────────

@router.message(F.text == "💪 Тренировка")
async def menu_workout(message: Message, state: FSMContext) -> None:
    if not await _check_access(message):
        return
    uid = str(message.from_user.id)
    active = await get_active_workout(uid)
    if active:
        from app.bot.handlers.workout import show_active_session
        await show_active_session(message, active, uid)
        return
    today_plan = await get_today_plan(uid)
    await message.answer(
        "Начинаем тренировку?" + ("\n\n📅 На сегодня есть план" if today_plan else ""),
        reply_markup=workout_start(has_plan=bool(today_plan)),
    )


@router.message(F.text == "📅 Планы")
async def menu_plans(message: Message, state: FSMContext) -> None:
    if not await _check_access(message):
        return
    await state.clear()
    await message.answer("📅 Управление планами:", reply_markup=plan_menu())


@router.message(F.text == "📖 История")
async def menu_history(message: Message, state: FSMContext) -> None:
    if not await _check_access(message):
        return
    await state.clear()
    await message.answer("📖 История тренировок:", reply_markup=history_menu())


# ─────────────────────── back callbacks ──────────────────────────────────────

@router.callback_query(F.data == "back:main")
async def back_to_main(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Главное меню:", reply_markup=None)
    await cb.message.answer("Выбери раздел:", reply_markup=main_menu())
    await cb.answer()


@router.callback_query(F.data == "back:plan")
async def back_to_plan(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("📅 Управление планами:", reply_markup=plan_menu())
    await cb.answer()


@router.callback_query(F.data == "back:history")
async def back_to_history(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("📖 История тренировок:", reply_markup=history_menu())
    await cb.answer()
