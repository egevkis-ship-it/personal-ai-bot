"""
Active workout FSM handler.

States:
  WorkoutStates.active       — waiting for set input (text/voice)
  WorkoutStates.confirm_set  — user sees parsed set, confirm/edit/cancel
  WorkoutStates.confirm_finish — confirm ending workout

Flow:
  1. Start workout (from plan or free) → create workouts row → enter active state
  2. Any text/voice → parse → show confirmation → confirm → add_set rows
  3. "Показать сессию" / menu buttons
  4. Finish → confirm → finish_workout
"""
from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ContentType, Message

from app.bot.keyboards import (
    confirm_sets,
    finish_workout_confirm,
    main_menu,
    workout_active_menu,
)
from app.bot.services.ai_parser import parse_set_text_ai, transcribe_voice
from app.bot.services.formatter import (
    format_active_session,
    format_set_confirmation,
)
from app.bot.services.set_parser import looks_like_exercise_input, parse_exercise_input
from app.bot.states import WorkoutStates
from app.db import (
    add_set,
    create_workout,
    finish_workout,
    get_active_workout,
    get_last_set,
    get_today_plan,
    get_workout,
    get_workout_sets,
    delete_set,
)

log = logging.getLogger(__name__)
router = Router(name="workout")

# ─────────────────────────── start workout ───────────────────────────────────

async def _start_workout(
    message_or_cb,
    uid: str,
    state: FSMContext,
    *,
    from_plan: bool = False,
) -> None:
    """Create workout row, enter active state, send start message."""
    plan = await get_today_plan(uid) if from_plan else None
    plan_id = plan["id"] if plan else None
    focus = plan.get("focus_label") if plan else None

    workout_id = await create_workout(
        user_id=uid,
        workout_date=date.today(),
        focus_label=focus,
        planned_workout_id=plan_id,
    )
    await state.update_data(workout_id=workout_id, last_exercise=None)
    await state.set_state(WorkoutStates.active)

    focus_str = f" — {focus}" if focus else ""
    text = (
        f"🏋️ Тренировка начата{focus_str}!\n\n"
        "Отправляй подходы в любом формате:\n"
        "  <i>Жим 80×10</i>\n"
        "  <i>Приседания 100 кг 4×8</i>\n"
        "  <i>Планка 60 сек</i>\n"
        "  <i>Подтягивания 10 8 7</i>\n\n"
        "Или записывай голосом 🎤"
    )
    send = message_or_cb.message if isinstance(message_or_cb, CallbackQuery) else message_or_cb
    await send.answer(text, parse_mode="HTML", reply_markup=workout_active_menu())
    if isinstance(message_or_cb, CallbackQuery):
        await message_or_cb.answer()


@router.callback_query(F.data == "workout:start_planned")
async def cb_start_planned(cb: CallbackQuery, state: FSMContext) -> None:
    await _start_workout(cb, str(cb.from_user.id), state, from_plan=True)


@router.callback_query(F.data == "workout:start_free")
async def cb_start_free(cb: CallbackQuery, state: FSMContext) -> None:
    await _start_workout(cb, str(cb.from_user.id), state, from_plan=False)


# ─────────────────────────── show active session ─────────────────────────────

async def show_active_session(
    message: Message,
    workout: dict,
    uid: str,
) -> None:
    """Display current session state and the workout menu."""
    sets = await get_workout_sets(workout["id"])
    text = format_active_session(sets, workout)
    await message.answer(text, parse_mode="HTML", reply_markup=workout_active_menu())


@router.callback_query(F.data == "workout:show", WorkoutStates.active)
async def cb_show_session(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    workout_id = data.get("workout_id")
    if not workout_id:
        await cb.answer("Нет активной тренировки", show_alert=True)
        return
    await cb.answer()
    workout = await get_workout(workout_id)
    sets = await get_workout_sets(workout_id)
    text = format_active_session(sets, workout)
    await cb.message.answer(text, parse_mode="HTML", reply_markup=workout_active_menu())


# ─────────────────────────── delete last set ────────────────────────────────

@router.callback_query(F.data == "workout:delete_last", WorkoutStates.active)
async def cb_delete_last(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    workout_id = data.get("workout_id")
    if not workout_id:
        await cb.answer("Нет активной тренировки", show_alert=True)
        return
    last = await get_last_set(workout_id)
    if not last:
        await cb.answer("Нет подходов для удаления", show_alert=True)
        return
    await cb.answer()
    await delete_set(last["id"])
    name = last.get("exercise_name", "?")
    await cb.message.answer(f"🗑 Удалён последний подход: <b>{name}</b>", parse_mode="HTML")


# ─────────────────────────── set input (text) ────────────────────────────────

@router.message(WorkoutStates.active, F.text)
async def handle_set_text(message: Message, state: FSMContext) -> None:
    await _handle_set_input(message, message.text, state)


@router.message(WorkoutStates.active, F.voice)
async def handle_set_voice(message: Message, state: FSMContext) -> None:
    voice = message.voice
    bot = message.bot
    try:
        file = await bot.get_file(voice.file_id)
        file_io = await bot.download_file(file.file_path)  # BytesIO, NOT awaitable
        ogg_bytes = file_io.read() if hasattr(file_io, "read") else bytes(file_io)
    except Exception as exc:
        log.exception("voice download error")
        await message.answer(f"❌ Не удалось скачать аудио: {exc}")
        return

    text = await transcribe_voice(ogg_bytes, filename="voice.ogg")
    if not text:
        await message.answer("❌ Не удалось распознать голос. Попробуй ещё раз.")
        return
    await message.answer(f"🎤 Распознано: <i>{text}</i>", parse_mode="HTML")
    await _handle_set_input(message, text, state)


async def _handle_set_input(message: Message, text: str, state: FSMContext) -> None:
    data = await state.get_data()
    workout_id = data.get("workout_id")
    last_exercise = data.get("last_exercise")

    # Quick-parse with pure-text parser
    sets = None
    if looks_like_exercise_input(text):
        sets = parse_exercise_input(text, last_exercise=last_exercise)

    # AI fallback if pure-text failed
    if not sets:
        ai_results = await parse_set_text_ai(text, exercise_hint=last_exercise)
        if ai_results:
            from app.bot.services.set_parser import SetResult
            sets = []
            for d in ai_results:
                sets.append(SetResult(
                    exercise_name=d.get("exercise_name") or last_exercise or "Упражнение",
                    weight_kg=d.get("weight_kg"),
                    reps=d.get("reps"),
                    reps_text=d.get("reps_text"),
                    duration_seconds=d.get("duration_seconds"),
                    is_warmup=d.get("is_warmup", False),
                    is_failure=d.get("is_failure", False),
                ))

    if not sets:
        await message.answer(
            "🤔 Не понял запись. Попробуй формат:\n"
            "  <i>Жим 80×10</i> или <i>Приседания 100кг 4х8</i>",
            parse_mode="HTML",
        )
        return

    # Save parsed sets to state for confirmation
    sets_dicts = [
        {
            "exercise_name": s.exercise_name,
            "weight_kg": s.weight_kg,
            "reps": s.reps,
            "reps_text": s.reps_text,
            "duration_seconds": s.duration_seconds,
            "is_warmup": s.is_warmup,
            "is_failure": s.is_failure,
            "superset_group": s.superset_group,
        }
        for s in sets
    ]
    await state.update_data(pending_sets=sets_dicts)
    await state.set_state(WorkoutStates.confirm_set)

    summary = format_set_confirmation(sets_dicts)
    await message.answer(
        f"{summary}\n\nЗаписать?",
        parse_mode="HTML",
        reply_markup=confirm_sets(summary),
    )


# ─────────────────────────── confirm set ────────────────────────────────────

@router.callback_query(F.data == "set:confirm", WorkoutStates.confirm_set)
async def cb_confirm_set(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    workout_id = data.get("workout_id")
    pending = data.get("pending_sets", [])

    last_ex = None
    for s in pending:
        await add_set(
            workout_id=workout_id,
            exercise_name=s["exercise_name"],
            weight_kg=s.get("weight_kg"),
            reps=s.get("reps"),
            reps_text=s.get("reps_text"),
            duration_seconds=s.get("duration_seconds"),
            is_warmup=s.get("is_warmup", False),
            is_failure=s.get("is_failure", False),
            superset_group=s.get("superset_group"),
        )
        last_ex = s["exercise_name"]

    await state.update_data(pending_sets=[], last_exercise=last_ex)
    await state.set_state(WorkoutStates.active)

    await cb.message.edit_reply_markup(reply_markup=None)
    count = len(pending)
    await cb.message.answer(
        f"✅ Записано {count} подх." if count > 1 else "✅ Записано!",
        reply_markup=workout_active_menu(),
    )


@router.callback_query(F.data == "set:edit", WorkoutStates.confirm_set)
async def cb_edit_set(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(WorkoutStates.active)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "✏️ Отправь исправленную запись:",
        reply_markup=workout_active_menu(),
    )


@router.callback_query(F.data == "set:cancel", WorkoutStates.confirm_set)
async def cb_cancel_set(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.update_data(pending_sets=[])
    await state.set_state(WorkoutStates.active)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("❌ Отменено.", reply_markup=workout_active_menu())


# ─────────────────────────── finish workout ──────────────────────────────────

@router.callback_query(F.data == "workout:finish", WorkoutStates.active)
async def cb_finish_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    workout_id = data.get("workout_id")
    sets = await get_workout_sets(workout_id)
    count = len(sets)
    await state.set_state(WorkoutStates.confirm_finish)
    await cb.message.answer(
        f"Завершить тренировку?\nЗаписано подходов: {count}",
        reply_markup=finish_workout_confirm(),
    )


@router.callback_query(F.data == "workout:finish_yes", WorkoutStates.confirm_finish)
async def cb_finish_yes(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    workout_id = data.get("workout_id")
    await finish_workout(workout_id)
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "🏁 Тренировка завершена! Отличная работа 💪",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "workout:finish_no", WorkoutStates.confirm_finish)
async def cb_finish_no(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkoutStates.active)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("↩️ Продолжаем!", reply_markup=workout_active_menu())
    await cb.answer()
