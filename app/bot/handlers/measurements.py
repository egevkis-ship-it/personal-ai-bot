"""
Body measurements FSM handler.

Flow:
  /Замеры → menu (New | History | Back)
  New → enter_input → text or voice
       → parse → confirm with values + missing list
       → fill_next (one-by-one) OR save
"""
from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    main_menu,
    measurement_confirm,
    measurement_skip_field,
    measurements_menu,
)
from app.bot.services.ai_parser import transcribe_voice
from app.bot.services.measurement_parser import (
    ALL_FIELDS,
    field_label_ru,
    missing_fields,
    parse_measurement,
)
from app.bot.states import MeasurementStates
from app.db import (
    create_measurement,
    get_last_measurement,
    get_measurements,
)

log = logging.getLogger(__name__)
router = Router(name="measurements")


# ─────────────────────────── entry ────────────────────────────────────────

@router.message(F.text == "📏 Замеры")
async def menu_measurements(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = str(message.from_user.id)
    last = await get_last_measurement(uid)
    intro = "📏 <b>Замеры тела</b>"
    if last:
        intro += (
            f"\n\nПоследние ({last['taken_on']}):\n{_format_measurement_short(last)}"
        )
    await message.answer(intro, parse_mode="HTML",
                         reply_markup=measurements_menu(has_history=bool(last)))


def _format_measurement_short(m: dict) -> str:
    lines = []
    for f in ALL_FIELDS:
        v = m.get(f)
        if v is None:
            continue
        unit = "кг" if f == "weight_kg" else "см"
        lines.append(f"  • {field_label_ru(f)}: <b>{float(v):g}</b> {unit}")
    if m.get("notes"):
        lines.append(f"  💬 <i>{m['notes']}</i>")
    return "\n".join(lines) if lines else "<i>пусто</i>"


def _format_full_with_missing(parsed: dict) -> str:
    vals = parsed.get("values", {})
    d = parsed.get("date") or date.today().isoformat()
    lines = [f"📅 <b>{d}</b>"]
    for f in ALL_FIELDS:
        v = vals.get(f)
        unit = "кг" if f == "weight_kg" else "см"
        if v is None:
            lines.append(f"  • {field_label_ru(f)}: <i>—</i>")
        else:
            lines.append(f"  • {field_label_ru(f)}: <b>{float(v):g}</b> {unit}")
    miss = missing_fields(vals)
    if miss:
        lines.append(f"\n<i>не указано: {len(miss)}</i>")
    return "\n".join(lines)


# ─────────────────────────── new entry ────────────────────────────────────

@router.callback_query(F.data == "meas:new")
async def cb_meas_new(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await state.set_state(MeasurementStates.enter_input)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "📏 Отправь замеры одним сообщением (текст или голос).\n\n"
        "Пример:\n"
        "<i>сегодня вес 102.7, голень 44.5, бедро 68, бёдра 104, "
        "живот 95, талия 92.5, грудь 106, рука 41, шея 40</i>\n\n"
        "Можно указать дату (<i>24.05.2026: ...</i> или <i>вчера: ...</i>) "
        "или пропустить — возьмём сегодня.",
        parse_mode="HTML",
    )


@router.message(MeasurementStates.enter_input, F.text)
async def handle_meas_text(message: Message, state: FSMContext) -> None:
    await _process_measurement_text(message, state, message.text)


@router.message(MeasurementStates.enter_input, F.voice)
async def handle_meas_voice(message: Message, state: FSMContext) -> None:
    voice = message.voice
    bot = message.bot
    try:
        file = await bot.get_file(voice.file_id)
        file_io = await bot.download_file(file.file_path)
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
    await _process_measurement_text(message, state, text)


async def _process_measurement_text(message: Message, state: FSMContext, text: str) -> None:
    parsed = await parse_measurement(text)
    if not parsed.get("values"):
        await message.answer(
            "❌ Не нашёл ни одного замера в сообщении. Попробуй ещё раз или нажми /start."
        )
        return
    await state.update_data(meas_parsed=parsed)
    await state.set_state(MeasurementStates.confirm)
    miss = missing_fields(parsed["values"])
    summary = _format_full_with_missing(parsed)
    await message.answer(
        summary + "\n\nЧто делаем?",
        parse_mode="HTML",
        reply_markup=measurement_confirm(missing_count=len(miss)),
    )


# ─────────────────────────── fill missing ─────────────────────────────────

@router.callback_query(F.data == "meas:fill_next", MeasurementStates.confirm)
async def cb_meas_fill_next(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    parsed = data.get("meas_parsed", {})
    miss = missing_fields(parsed.get("values", {}))
    if not miss:
        # nothing left — go straight to save
        await _save_and_finish(cb.message, state)
        return
    next_field = miss[0]
    await state.update_data(meas_filling_field=next_field)
    await state.set_state(MeasurementStates.fill_field)
    unit = "кг" if next_field == "weight_kg" else "см"
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        f"Введи значение для <b>{field_label_ru(next_field)}</b> ({unit}). "
        f"Просто число.",
        parse_mode="HTML",
        reply_markup=measurement_skip_field(),
    )


@router.message(MeasurementStates.fill_field, F.text)
async def handle_meas_fill_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("meas_filling_field")
    parsed = data.get("meas_parsed", {})
    raw = (message.text or "").strip().replace(",", ".")
    try:
        val = float(raw.split()[0])
    except (ValueError, IndexError):
        await message.answer("❌ Не понял число. Отправь только цифру, например <i>92.5</i>.",
                             parse_mode="HTML", reply_markup=measurement_skip_field())
        return
    parsed.setdefault("values", {})[field] = val
    await state.update_data(meas_parsed=parsed, meas_filling_field=None)
    await state.set_state(MeasurementStates.confirm)
    miss = missing_fields(parsed["values"])
    await message.answer(
        _format_full_with_missing(parsed) + "\n\nЧто делаем?",
        parse_mode="HTML",
        reply_markup=measurement_confirm(missing_count=len(miss)),
    )


@router.callback_query(F.data == "meas:skip_field", MeasurementStates.fill_field)
async def cb_meas_skip_field(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    parsed = data.get("meas_parsed", {})
    field = data.get("meas_filling_field")
    skipped: list = data.get("meas_skipped_fields") or []
    if field and field not in skipped:
        skipped.append(field)
    await state.update_data(meas_skipped_fields=skipped, meas_filling_field=None)
    # Move to next missing that isn't already skipped
    miss = [f for f in missing_fields(parsed.get("values", {})) if f not in skipped]
    if miss:
        next_field = miss[0]
        await state.update_data(meas_filling_field=next_field)
        await state.set_state(MeasurementStates.fill_field)
        unit = "кг" if next_field == "weight_kg" else "см"
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.answer(
            f"Введи значение для <b>{field_label_ru(next_field)}</b> ({unit}).",
            parse_mode="HTML",
            reply_markup=measurement_skip_field(),
        )
        return
    # No more missing → back to confirm
    await state.set_state(MeasurementStates.confirm)
    miss_show = missing_fields(parsed.get("values", {}))
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        _format_full_with_missing(parsed) + "\n\nЧто делаем?",
        parse_mode="HTML",
        reply_markup=measurement_confirm(missing_count=len(miss_show)),
    )


# ─────────────────────────── save / cancel ─────────────────────────────────

@router.callback_query(F.data == "meas:save", MeasurementStates.confirm)
async def cb_meas_save(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _save_and_finish(cb.message, state, user_id=str(cb.from_user.id))


async def _save_and_finish(message: Message, state: FSMContext, user_id: str | None = None) -> None:
    data = await state.get_data()
    parsed = data.get("meas_parsed", {})
    if not parsed:
        await message.answer("❌ Данные потеряны.", reply_markup=main_menu())
        await state.clear()
        return
    uid = user_id or str(message.from_user.id)
    taken_on = parsed.get("date") or date.today().isoformat()
    values = parsed.get("values", {})
    notes = parsed.get("notes")
    try:
        await create_measurement(uid, taken_on, values, notes=notes)
    except Exception as exc:
        log.exception("save measurement failed")
        import html as _html
        await message.answer(f"❌ Не сохранилось: <code>{_html.escape(str(exc))[:200]}</code>",
                             parse_mode="HTML", reply_markup=main_menu())
        await state.clear()
        return
    await state.clear()
    n_saved = len(values)
    await message.answer(
        f"✅ Замеры сохранены ({n_saved} параметров).",
        reply_markup=measurements_menu(has_history=True),
    )


@router.callback_query(F.data == "meas:cancel")
async def cb_meas_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("❌ Отменено.", reply_markup=main_menu())


# ─────────────────────────── history ───────────────────────────────────────

@router.callback_query(F.data == "meas:history")
async def cb_meas_history(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    uid = str(cb.from_user.id)
    rows = await get_measurements(uid, limit=12)
    if not rows:
        await cb.message.edit_text("📭 Нет замеров.", reply_markup=measurements_menu(False))
        return
    # Show table-like list, newest first; deltas vs previous
    lines = ["📊 <b>История замеров</b>\n"]
    for i, m in enumerate(rows):
        prev = rows[i + 1] if i + 1 < len(rows) else None
        d = m["taken_on"]
        block = [f"📅 <b>{d}</b>"]
        for f in ALL_FIELDS:
            v = m.get(f)
            if v is None:
                continue
            unit = "кг" if f == "weight_kg" else "см"
            delta = ""
            if prev and prev.get(f) is not None:
                diff = float(v) - float(prev[f])
                if abs(diff) >= 0.05:
                    sign = "+" if diff > 0 else ""
                    delta = f" ({sign}{diff:g})"
            block.append(f"  {field_label_ru(f)}: <b>{float(v):g}</b> {unit}{delta}")
        if m.get("notes"):
            block.append(f"  💬 <i>{m['notes']}</i>")
        lines.append("\n".join(block))
    text = "\n\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n\n<i>...обрезано</i>"
    await cb.message.edit_text(text, parse_mode="HTML",
                               reply_markup=measurements_menu(has_history=True))
