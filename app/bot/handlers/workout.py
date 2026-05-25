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


# ─────────────────────────── note to last set ───────────────────────────────

@router.callback_query(F.data == "workout:note_last", WorkoutStates.active)
async def cb_note_last(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    workout_id = data.get("workout_id")
    if not workout_id:
        await cb.answer("Нет активной тренировки", show_alert=True)
        return
    last = await get_last_set(workout_id)
    if not last:
        await cb.answer("Нет подходов", show_alert=True)
        return
    await cb.answer()
    from app.bot.keyboards import skip_or_cancel
    await state.update_data(note_target_set_id=last["id"])
    await state.set_state(WorkoutStates.enter_set_note)
    existing = last.get("notes") or ""
    extra = f"\n\nТекущий: <i>{existing}</i>" if existing else ""
    await cb.message.answer(
        f"✏️ Комментарий к подходу <b>{last.get('exercise_name','?')}</b> "
        f"(вес {last.get('weight_kg') or '?'}, повт {last.get('reps') or '?'})."
        f"{extra}\n\nОтправь текст одним сообщением:",
        parse_mode="HTML",
        reply_markup=skip_or_cancel(),
    )


async def _download_voice_text(message: Message) -> str | None:
    """Telethon-/aiogram-3 voice → ogg bytes → Whisper text."""
    voice = message.voice
    bot = message.bot
    try:
        file = await bot.get_file(voice.file_id)
        file_io = await bot.download_file(file.file_path)
        ogg_bytes = file_io.read() if hasattr(file_io, "read") else bytes(file_io)
    except Exception as exc:
        log.exception("voice download error")
        await message.answer(f"❌ Не удалось скачать аудио: {exc}")
        return None
    text = await transcribe_voice(ogg_bytes, filename="voice.ogg")
    if not text:
        await message.answer("❌ Не удалось распознать голос. Попробуй ещё раз.")
        return None
    return text


async def _save_set_note(state: FSMContext, message: Message, text: str) -> None:
    from app.db import update_set
    data = await state.get_data()
    set_id = data.get("note_target_set_id")
    if not set_id:
        await message.answer("Не нашёл подход. Отменено.", reply_markup=workout_active_menu())
        await state.set_state(WorkoutStates.active)
        return
    await update_set(set_id, notes=text.strip())
    await state.update_data(note_target_set_id=None)
    await state.set_state(WorkoutStates.active)
    await message.answer(
        f"✅ Коммент сохранён: <i>{text.strip()}</i>",
        parse_mode="HTML",
        reply_markup=workout_active_menu(),
    )


@router.message(WorkoutStates.enter_set_note, F.text)
async def handle_set_note_text(message: Message, state: FSMContext) -> None:
    await _save_set_note(state, message, message.text)


@router.message(WorkoutStates.enter_set_note, F.voice)
async def handle_set_note_voice(message: Message, state: FSMContext) -> None:
    text = await _download_voice_text(message)
    if not text:
        return
    await message.answer(f"🎤 Распознано: <i>{text}</i>", parse_mode="HTML")
    await _save_set_note(state, message, text)


@router.callback_query(F.data == "note:skip", WorkoutStates.enter_set_note)
@router.callback_query(F.data == "note:cancel", WorkoutStates.enter_set_note)
async def cb_set_note_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.update_data(note_target_set_id=None)
    await state.set_state(WorkoutStates.active)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("↩️ Без комментария.", reply_markup=workout_active_menu())


# ─────────────────────────── set input (text) ────────────────────────────────

@router.message(WorkoutStates.active, F.text)
async def handle_set_text(message: Message, state: FSMContext) -> None:
    await _handle_set_input(message, message.text, state)


@router.message(WorkoutStates.active, F.voice)
async def handle_set_voice(message: Message, state: FSMContext) -> None:
    text = await _download_voice_text(message)
    if not text:
        return
    await message.answer(f"🎤 Распознано: <i>{text}</i>", parse_mode="HTML")
    # Voice → conversational speech with "первый подход / повторений / килограмм"
    # → pure-text parser misreads. Force AI-first for voice input.
    await _handle_set_input(message, text, state, prefer_ai=True)


# Conversational-speech markers that signal AI must do the parsing
import re as _re
_SPEECH_MARKERS = _re.compile(
    r"\b(первый|второй|третий|четвертый|пятый|шестой|"
    r"подход|подхода|подходов|"
    r"повторение|повторения|повторений|"
    r"килограмм\w*)\b",
    _re.IGNORECASE,
)


async def _handle_set_input(message: Message, text: str, state: FSMContext, *, prefer_ai: bool = False) -> None:
    data = await state.get_data()
    workout_id = data.get("workout_id")
    last_exercise = data.get("last_exercise")

    # Voice / conversational input → AI-first path
    is_conversational = prefer_ai or bool(_SPEECH_MARKERS.search(text))

    sets = None
    if not is_conversational and looks_like_exercise_input(text):
        sets = parse_exercise_input(text, last_exercise=last_exercise)

    # AI fallback (or AI-first when conversational)
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

    # Last-resort: try pure parser even on conversational text
    if not sets and is_conversational and looks_like_exercise_input(text):
        sets = parse_exercise_input(text, last_exercise=last_exercise)

    if not sets:
        await message.answer(
            "🤔 Не понял запись. Попробуй формат:\n"
            "  <i>Жим 80×10</i> или <i>Приседания 100кг 4х8</i>",
            parse_mode="HTML",
        )
        return

    # Normalize exercise names through the catalog BEFORE showing the confirm
    # dialog. This way user sees the canonical name ("Жим на грудь в тренажёре")
    # in the preview, not the raw guess from the AI parser ("Жим в тренажёре"),
    # and can catch wrong-catalog mappings before saving.
    from app.bot.services.exercise_catalog import resolve_or_register
    name_cache: dict[str, str] = {}
    unique_names = {s.exercise_name for s in sets if s.exercise_name}
    for raw_name in unique_names:
        try:
            r = await resolve_or_register(raw_name)
            name_cache[raw_name] = r.get("canonical") or raw_name
        except Exception as exc:
            log.warning("normalize failed for %r: %s", raw_name, exc)
            name_cache[raw_name] = raw_name
    for s in sets:
        if s.exercise_name in name_cache:
            s.exercise_name = name_cache[s.exercise_name]

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

    # Exercise names were already normalized in _handle_set_input before
    # the confirm dialog was shown — write them as-is.
    last_ex = None
    for s in pending:
        ex_name = s["exercise_name"]
        await add_set(
            workout_id=workout_id,
            exercise_name=ex_name,
            weight_kg=s.get("weight_kg"),
            reps=s.get("reps"),
            reps_text=s.get("reps_text"),
            duration_seconds=s.get("duration_seconds"),
            is_warmup=s.get("is_warmup", False),
            is_failure=s.get("is_failure", False),
            superset_group=s.get("superset_group"),
        )
        last_ex = ex_name

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

    # Stay in flow so user can leave a comment, see AI coach, plan next.
    await state.set_state(WorkoutStates.enter_workout_note)
    await state.update_data(finished_workout_id=workout_id)

    await cb.message.edit_reply_markup(reply_markup=None)
    from app.bot.keyboards import skip_or_cancel
    await cb.message.answer(
        "🏁 Тренировка завершена! Отличная работа 💪\n\n"
        "✏️ Оставить комментарий к тренировке? Отправь текстом, или нажми «Пропустить».",
        reply_markup=skip_or_cancel(skip_cb="finish:note_skip", cancel_cb="finish:note_skip"),
    )


async def _save_workout_note(state: FSMContext, message: Message, text: str) -> None:
    from app.db import update_workout_notes
    from app.bot.keyboards import post_finish_menu
    data = await state.get_data()
    wid = data.get("finished_workout_id")
    if wid:
        try:
            await update_workout_notes(wid, text.strip())
        except Exception as exc:
            log.warning("save workout note failed: %s", exc)
    await message.answer(
        f"✅ Комментарий сохранён: <i>{text.strip()}</i>",
        parse_mode="HTML",
        reply_markup=post_finish_menu(),
    )


@router.message(WorkoutStates.enter_workout_note, F.text)
async def handle_workout_note(message: Message, state: FSMContext) -> None:
    await _save_workout_note(state, message, message.text)


@router.message(WorkoutStates.enter_workout_note, F.voice)
async def handle_workout_note_voice(message: Message, state: FSMContext) -> None:
    text = await _download_voice_text(message)
    if not text:
        return
    await message.answer(f"🎤 Распознано: <i>{text}</i>", parse_mode="HTML")
    await _save_workout_note(state, message, text)


@router.callback_query(F.data == "finish:note_skip", WorkoutStates.enter_workout_note)
async def cb_finish_note_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.bot.keyboards import post_finish_menu
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Без комментария.", reply_markup=post_finish_menu())


# Final exit from post-finish flow (returns to main menu)
@router.callback_query(F.data == "finish:done")
async def cb_finish_done(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Главное меню:", reply_markup=main_menu())


@router.callback_query(F.data == "finish:coach")
async def cb_finish_coach(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.bot.keyboards import post_finish_menu
    from app.bot.services.coach import build_coach_facts, opus_coach_summary

    data = await state.get_data()
    workout_id = data.get("finished_workout_id")
    if not workout_id:
        # fall back to user's most recent finished workout
        from app.db import get_last_workout
        last = await get_last_workout(str(cb.from_user.id))
        if last:
            workout_id = last["id"]
    if not workout_id:
        await cb.message.answer("Не нашёл тренировки для разбора.", reply_markup=post_finish_menu())
        return

    placeholder = await cb.message.answer("🧮 Считаю метрики…")
    try:
        facts = await build_coach_facts(str(cb.from_user.id), workout_id)
        summary = await opus_coach_summary(facts)
    except Exception as exc:
        log.exception("coach error")
        summary = f"Не удалось получить разбор: {exc}"

    # Replace placeholder with the actual text + menu
    try:
        await placeholder.edit_text(summary, parse_mode=None, reply_markup=None)
    except Exception:
        await cb.message.answer(summary)
    await cb.message.answer("Что дальше?", reply_markup=post_finish_menu())


@router.callback_query(F.data == "finish:plan_next")
async def cb_finish_plan_next(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.bot.keyboards import plan_next_mode
    await state.set_state(WorkoutStates.plan_next_mode)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Как спланировать?", reply_markup=plan_next_mode())


@router.callback_query(F.data == "plan_next:manual")
async def cb_plan_next_manual(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.bot.keyboards import plan_menu
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("📅 Управление планами:", reply_markup=plan_menu())


@router.callback_query(F.data == "plan_next:ai")
async def cb_plan_next_ai(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.bot.keyboards import ai_plan_period
    await state.set_state(WorkoutStates.ai_plan_period)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("На какой период?", reply_markup=ai_plan_period())


# ─── pick day ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ai_plan:day")
async def cb_ai_plan_day(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.bot.keyboards import ai_plan_day_picker
    from app.db import get_planned_workouts_range
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    # Mon..Sun of *next* week
    # weekday(): Mon=0 ... Sun=6 — compute start of next week
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = today + _td(days=days_until_monday)
    days = [next_monday + _td(days=i) for i in range(7)]

    uid = str(cb.from_user.id)
    plans = await get_planned_workouts_range(uid, days[0], days[-1])
    busy = {p["planned_date"]: (p.get("focus_label") or "занят") for p in plans}

    _DAYS_RU = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}
    items = []
    for d in days:
        label = f"{_DAYS_RU[d.weekday()]} {d.day:02d}.{d.month:02d}"
        # convert busy key potentially being a date or string
        b = busy.get(d) or busy.get(d.isoformat())
        items.append((d.isoformat(), label, b))

    await state.set_state(WorkoutStates.ai_plan_pick_day)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Выбери день следующей недели:", reply_markup=ai_plan_day_picker(items))


@router.callback_query(F.data.startswith("ai_plan:date:"), WorkoutStates.ai_plan_pick_day)
async def cb_ai_plan_pick(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.db import get_planned_workouts_range
    from app.bot.keyboards import occupied_day_choice
    from datetime import date as _date

    iso = cb.data.split(":", 2)[2]
    target = _date.fromisoformat(iso)
    uid = str(cb.from_user.id)
    existing = await get_planned_workouts_range(uid, target, target)
    if existing:
        focus = existing[0].get("focus_label") or "тренировка"
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.answer(
            f"📌 На {iso} уже есть тренировка ({focus}). Что делаем?",
            reply_markup=occupied_day_choice(iso),
        )
        return
    await _generate_and_present_day(cb, state, target, replace=False)


@router.callback_query(F.data.startswith("ai_plan:gen:"))
async def cb_ai_plan_gen(cb: CallbackQuery, state: FSMContext) -> None:
    """ai_plan:gen:<iso>:replace — regenerate over an occupied day."""
    await cb.answer()
    from datetime import date as _date
    parts = cb.data.split(":")
    iso = parts[2]
    target = _date.fromisoformat(iso)
    replace = parts[3] == "replace" if len(parts) > 3 else False
    await _generate_and_present_day(cb, state, target, replace=replace)


async def _generate_and_present_day(cb: CallbackQuery, state: FSMContext, target_date, replace: bool):
    from app.bot.services.planner import generate_day_plan
    from app.bot.services.formatter import fmt_date, _esc
    from app.bot.keyboards import ai_plan_confirm

    placeholder = await cb.message.answer("🧠 Генерирую тренировку…")
    plan = await generate_day_plan(str(cb.from_user.id), target_date)
    if not plan:
        try:
            await placeholder.edit_text("❌ Не удалось сгенерировать план.")
        except Exception:
            pass
        from app.bot.keyboards import post_finish_menu
        await cb.message.answer("Что дальше?", reply_markup=post_finish_menu())
        return

    # Build preview text
    lines = [f"📅 <b>{fmt_date(target_date)} — {_esc(plan.focus_label or 'тренировка')}</b>"]
    if plan.notes:
        lines.append(f"💬 <i>{_esc(plan.notes)}</i>")
    for ex in plan.exercises:
        r_min, r_max = ex.target_reps_min, ex.target_reps_max
        if r_min and r_max and r_min != r_max:
            reps = f"{r_min}–{r_max}"
        elif r_min:
            reps = str(r_min)
        else:
            reps = ex.reps_text or "?"
        w = f" · {ex.target_weight:g} кг" if ex.target_weight else ""
        lines.append(f"  • {_esc(ex.name)} — {ex.target_sets or '?'}×{reps}{w}")
        if ex.notes:
            lines.append(f"    <i>💬 {_esc(ex.notes)}</i>")

    # Persist the generated plan in FSM for save step
    await state.update_data(ai_plan={
        "focus_label": plan.focus_label,
        "notes": plan.notes,
        "exercises": [{
            "name": e.name,
            "target_sets": e.target_sets,
            "target_reps_min": e.target_reps_min,
            "target_reps_max": e.target_reps_max,
            "target_weight": e.target_weight,
            "reps_text": e.reps_text,
            "notes": e.notes,
            "superset_group": e.superset_group,
        } for e in plan.exercises],
    })
    await state.set_state(WorkoutStates.ai_plan_confirm)

    try:
        await placeholder.edit_text("\n".join(lines), parse_mode="HTML",
                                    reply_markup=ai_plan_confirm(target_date.isoformat(), replace=replace))
    except Exception:
        await cb.message.answer("\n".join(lines), parse_mode="HTML",
                                reply_markup=ai_plan_confirm(target_date.isoformat(), replace=replace))


@router.callback_query(F.data.startswith("ai_plan:save:"), WorkoutStates.ai_plan_confirm)
async def cb_ai_plan_save(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.db import create_planned_workout, update_planned_workout_notes
    from datetime import date as _date

    iso = cb.data.split(":", 2)[2]
    target = _date.fromisoformat(iso)
    data = await state.get_data()
    plan_data = data.get("ai_plan", {})
    if not plan_data:
        await cb.message.answer("План не найден. Попробуй заново.")
        return
    uid = str(cb.from_user.id)
    plan_id = await create_planned_workout(
        user_id=uid,
        planned_date=target,
        focus_label=plan_data.get("focus_label"),
        exercises=plan_data.get("exercises", []),
    )
    if plan_data.get("notes"):
        try:
            await update_planned_workout_notes(plan_id, plan_data["notes"])
        except Exception:
            pass

    from app.bot.keyboards import post_finish_menu
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(f"✅ Тренировка на {iso} сохранена.", reply_markup=post_finish_menu())


@router.callback_query(F.data.startswith("ai_plan:save_replace:"), WorkoutStates.ai_plan_confirm)
async def cb_ai_plan_save_replace(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.db import create_planned_workout, update_planned_workout_notes, get_planned_workouts_range, delete_planned_workout
    from datetime import date as _date

    iso = cb.data.split(":", 2)[2]
    target = _date.fromisoformat(iso)
    uid = str(cb.from_user.id)
    # Soft-delete existing
    existing = await get_planned_workouts_range(uid, target, target)
    for e in existing:
        await delete_planned_workout(e["id"])

    data = await state.get_data()
    plan_data = data.get("ai_plan", {})
    if plan_data:
        plan_id = await create_planned_workout(
            user_id=uid,
            planned_date=target,
            focus_label=plan_data.get("focus_label"),
            exercises=plan_data.get("exercises", []),
        )
        if plan_data.get("notes"):
            try:
                await update_planned_workout_notes(plan_id, plan_data["notes"])
            except Exception:
                pass
    from app.bot.keyboards import post_finish_menu
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(f"✅ Тренировка на {iso} заменена.", reply_markup=post_finish_menu())


# ─── week ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ai_plan:week")
async def cb_ai_plan_week(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.bot.services.planner import generate_week_plan
    from app.bot.services.formatter import fmt_date, _esc
    from app.bot.keyboards import ai_plan_week_confirm
    from datetime import date as _date, timedelta as _td

    today = _date.today()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = today + _td(days=days_until_monday)

    placeholder = await cb.message.answer("🧠 Генерирую неделю…")
    week = await generate_week_plan(str(cb.from_user.id), next_monday)
    if not week:
        try:
            await placeholder.edit_text("❌ Не удалось сгенерировать неделю.")
        except Exception:
            pass
        from app.bot.keyboards import post_finish_menu
        await cb.message.answer("Что дальше?", reply_markup=post_finish_menu())
        return

    # Preview
    text_blocks = []
    days_assigned = []
    for i, day in enumerate(week[:7]):
        d = next_monday + _td(days=i)
        days_assigned.append(d.isoformat())
        focus = day.focus_label or ("отдых" if not day.exercises else "тренировка")
        block = [f"📅 <b>{fmt_date(d)} — {_esc(focus)}</b>"]
        if day.notes:
            block.append(f"💬 <i>{_esc(day.notes)}</i>")
        if not day.exercises:
            block.append("  <i>отдых</i>")
        for ex in day.exercises:
            r_min, r_max = ex.target_reps_min, ex.target_reps_max
            if r_min and r_max and r_min != r_max:
                reps = f"{r_min}–{r_max}"
            elif r_min:
                reps = str(r_min)
            else:
                reps = ex.reps_text or "?"
            w = f" · {ex.target_weight:g} кг" if ex.target_weight else ""
            block.append(f"  • {_esc(ex.name)} — {ex.target_sets or '?'}×{reps}{w}")
        text_blocks.append("\n".join(block))

    await state.update_data(ai_week={
        "start_date": next_monday.isoformat(),
        "days": [{
            "date": days_assigned[i],
            "focus_label": d.focus_label,
            "notes": d.notes,
            "exercises": [{
                "name": e.name,
                "target_sets": e.target_sets,
                "target_reps_min": e.target_reps_min,
                "target_reps_max": e.target_reps_max,
                "target_weight": e.target_weight,
                "reps_text": e.reps_text,
                "notes": e.notes,
                "superset_group": e.superset_group,
            } for e in d.exercises],
        } for i, d in enumerate(week[:7])],
    })
    await state.set_state(WorkoutStates.ai_plan_confirm)
    text = "\n\n".join(text_blocks)
    try:
        await placeholder.edit_text(text[:4000], parse_mode="HTML", reply_markup=ai_plan_week_confirm())
    except Exception:
        await cb.message.answer(text[:4000], parse_mode="HTML", reply_markup=ai_plan_week_confirm())


@router.callback_query(F.data == "ai_plan:save_week", WorkoutStates.ai_plan_confirm)
async def cb_ai_plan_save_week(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    from app.db import create_planned_workout, update_planned_workout_notes, get_planned_workouts_range, delete_planned_workout
    from datetime import date as _date

    data = await state.get_data()
    week = (data.get("ai_week") or {}).get("days", [])
    uid = str(cb.from_user.id)
    saved = 0
    for d in week:
        target = _date.fromisoformat(d["date"])
        # Replace any existing planned workout on that date
        existing = await get_planned_workouts_range(uid, target, target)
        for e in existing:
            await delete_planned_workout(e["id"])
        # Skip empty (rest) days
        if not d.get("exercises"):
            continue
        plan_id = await create_planned_workout(
            user_id=uid,
            planned_date=target,
            focus_label=d.get("focus_label"),
            exercises=d.get("exercises", []),
        )
        if d.get("notes"):
            try:
                await update_planned_workout_notes(plan_id, d["notes"])
            except Exception:
                pass
        saved += 1
    from app.bot.keyboards import post_finish_menu
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(f"✅ Неделя сохранена: {saved} тренировок.", reply_markup=post_finish_menu())


@router.callback_query(F.data == "workout:finish_no", WorkoutStates.confirm_finish)
async def cb_finish_no(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkoutStates.active)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("↩️ Продолжаем!", reply_markup=workout_active_menu())
    await cb.answer()
