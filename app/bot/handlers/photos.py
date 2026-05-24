"""
Progress photos handler — series-aware fitness analysis.

Flow:
  /Фото → menu (New | History | Back)
  New → waiting_photo
    → user sends 1..N photos (album or sequential)
    → bot collects them in FSM buffer, debounce 5s after the last one
    → user may also press "✅ Готово, проанализировать" to commit early
    → bot downloads all photos, sends them as ONE batch to Sonnet vision
      with the fitness skill prompt
    → saves each photo with the same series_id and the same shared report
    → optional text/voice comment → saved on the last photo
  History → list of photos with descriptions, paginated.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import (
    main_menu,
    photo_after_upload,
    photo_nav,
    photos_menu,
)
from app.bot.services.ai_parser import transcribe_voice
from app.bot.services.photo_vision import describe_photo_series
from app.bot.states import PhotoStates
from app.db import (
    create_photo,
    delete_photo,
    get_photo,
    get_photos,
    update_photo_notes,
)

log = logging.getLogger(__name__)
router = Router(name="photos")

_DEBOUNCE_SECONDS = 5.0


# ─────────────────────────── entry ────────────────────────────────────────

@router.message(F.text == "📸 Фото")
async def menu_photos(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = str(message.from_user.id)
    photos = await get_photos(uid, limit=1)
    intro = "📸 <b>Прогресс-фото</b>"
    if photos:
        last = photos[0]
        intro += f"\n\nПоследнее: {last['taken_on']}"
        if last.get("ai_description"):
            intro += f"\n<i>{last['ai_description'][:200]}...</i>"
    await message.answer(
        intro, parse_mode="HTML",
        reply_markup=photos_menu(has_history=bool(photos)),
    )


# ─────────────────────────── new flow ────────────────────────────────────

def _collecting_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Готово, проанализировать", callback_data="photo:commit"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="photo:cancel"),
    ]])


@router.callback_query(F.data == "photo:new")
async def cb_photo_new(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await state.set_state(PhotoStates.waiting_photo)
    await state.update_data(photo_buffer=[], debounce_token=0, status_msg_id=None)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "📷 Отправь фото (можно серию — несколько подряд или альбомом).\n\n"
        "Я подожду 5 секунд после последнего и пошлю всё в фитнес-анализ "
        "одним пакетом. Или нажми «Готово» когда закончишь.",
    )


@router.message(PhotoStates.waiting_photo, F.photo)
async def handle_photo_upload(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    data = await state.get_data()
    buf: list[dict] = list(data.get("photo_buffer") or [])
    buf.append({
        "file_id": photo.file_id,
        "file_unique_id": photo.file_unique_id,
    })
    token = int(data.get("debounce_token") or 0) + 1
    await state.update_data(photo_buffer=buf, debounce_token=token,
                            _uid=str(message.from_user.id))

    # Show / update single status message
    status_id = data.get("status_msg_id")
    status_text = (
        f"📸 Получено фото: <b>{len(buf)}</b>\n"
        f"⏳ Жду ещё {int(_DEBOUNCE_SECONDS)}с или нажми Готово."
    )
    try:
        if status_id:
            await message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=status_id,
                text=status_text, parse_mode="HTML", reply_markup=_collecting_kb(),
            )
        else:
            sent = await message.answer(status_text, parse_mode="HTML",
                                        reply_markup=_collecting_kb())
            await state.update_data(status_msg_id=sent.message_id)
    except Exception:
        # If edit fails (e.g. message too old) — send fresh
        sent = await message.answer(status_text, parse_mode="HTML",
                                    reply_markup=_collecting_kb())
        await state.update_data(status_msg_id=sent.message_id)

    # Fire-and-forget debounce
    asyncio.create_task(_debounce_and_commit(message, state, token))


async def _debounce_and_commit(message: Message, state: FSMContext, my_token: int) -> None:
    await asyncio.sleep(_DEBOUNCE_SECONDS)
    data = await state.get_data()
    if int(data.get("debounce_token") or 0) != my_token:
        return  # superseded by a newer photo
    await _commit_series(message, state)


@router.callback_query(F.data == "photo:commit", PhotoStates.waiting_photo)
async def cb_photo_commit(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    # Bump token so any pending debounce is cancelled
    data = await state.get_data()
    token = int(data.get("debounce_token") or 0) + 100
    await state.update_data(debounce_token=token)
    await _commit_series(cb.message, state)


@router.callback_query(F.data == "photo:cancel", PhotoStates.waiting_photo)
async def cb_photo_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("❌ Отменено.", reply_markup=photos_menu(has_history=True))


async def _commit_series(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    buf: list[dict] = list(data.get("photo_buffer") or [])
    if not buf:
        await message.answer("Нет фото для анализа.", reply_markup=photos_menu(False))
        await state.clear()
        return
    status_id = data.get("status_msg_id")
    bot = message.bot

    # Update status — analysing
    analyzing_text = (
        f"🧠 Анализирую серию ({len(buf)} фото) через AI Vision…\n"
        "Это занимает 10-30 секунд."
    )
    try:
        if status_id:
            await bot.edit_message_text(
                chat_id=message.chat.id, message_id=status_id,
                text=analyzing_text, reply_markup=None,
            )
    except Exception:
        await message.answer(analyzing_text)

    # Download every photo
    images: list[tuple[bytes, str]] = []
    for item in buf:
        try:
            file = await bot.get_file(item["file_id"])
            file_io = await bot.download_file(file.file_path)
            img = file_io.read() if hasattr(file_io, "read") else bytes(file_io)
            images.append((img, "image/jpeg"))
        except Exception as exc:
            log.warning("photo %s download failed: %s", item.get("file_id"), exc)

    if not images:
        msg = "❌ Не удалось скачать ни одно фото."
        try:
            if status_id:
                await bot.edit_message_text(chat_id=message.chat.id, message_id=status_id,
                                            text=msg)
            else:
                await message.answer(msg)
        except Exception:
            await message.answer(msg)
        await state.clear()
        return

    # ONE vision call for the whole series
    report = await describe_photo_series(images) or "<i>описание не получено</i>"

    # Persist every photo with the same series_id and the shared report
    series_id = secrets.token_hex(8)
    # message here can be either the user's photo message or the bot's status
    # message (from cb_photo_commit). Prefer FSM-stored user id if present.
    state_data = await state.get_data()
    uid = state_data.get("_uid") or (
        str(message.from_user.id) if message.from_user else str(message.chat.id)
    )
    saved_ids: list[int] = []
    for item in buf:
        try:
            pid = await _create_photo_with_series(
                user_id=uid,
                taken_on=date.today(),
                telegram_file_id=item["file_id"],
                telegram_file_unique_id=item.get("file_unique_id"),
                ai_description=report,
                series_id=series_id,
            )
            saved_ids.append(pid)
        except Exception as exc:
            log.exception("save photo failed: %s", exc)

    last_pid = saved_ids[-1] if saved_ids else None
    await state.update_data(last_photo_id=last_pid, photo_series_id=series_id,
                            photo_buffer=[], status_msg_id=None)
    await state.set_state(PhotoStates.waiting_note)

    final_text = (
        f"✅ Сохранено фото: <b>{len(saved_ids)}</b>\n\n"
        f"<b>Фитнес-анализ серии:</b>\n{report}\n\n"
        "Можешь дописать комментарий (текст или голос) или пропустить."
    )
    # Telegram caption limit irrelevant here — sending as text message
    try:
        if status_id:
            await bot.edit_message_text(
                chat_id=message.chat.id, message_id=status_id,
                text=final_text[:4000], parse_mode="HTML",
                reply_markup=photo_after_upload(),
            )
        else:
            await message.answer(final_text[:4000], parse_mode="HTML",
                                 reply_markup=photo_after_upload())
    except Exception:
        await message.answer(final_text[:4000], parse_mode="HTML",
                             reply_markup=photo_after_upload())


async def _create_photo_with_series(
    user_id: str,
    taken_on,
    telegram_file_id: str,
    telegram_file_unique_id: str | None,
    ai_description: str | None,
    series_id: str,
) -> int:
    """Like db.create_photo but also writes series_id."""
    from datetime import date as _date
    from sqlalchemy import text
    from app.db.engine import get_session
    d = taken_on if isinstance(taken_on, _date) else _date.fromisoformat(str(taken_on))
    async with get_session() as s:
        r = await s.execute(
            text("""
                INSERT INTO progress_photos
                    (user_id, taken_on, telegram_file_id, telegram_file_unique_id,
                     ai_description, series_id)
                VALUES (:uid, :d, :fid, :fuid, :desc, :sid)
                RETURNING id
            """),
            {"uid": user_id, "d": d, "fid": telegram_file_id,
             "fuid": telegram_file_unique_id, "desc": ai_description, "sid": series_id},
        )
        return r.scalar_one()


# ─────────────────────── post-upload note ────────────────────────────────

@router.message(PhotoStates.waiting_note, F.text)
async def handle_photo_note_text(message: Message, state: FSMContext) -> None:
    await _save_photo_note(state, message, message.text)


@router.message(PhotoStates.waiting_note, F.voice)
async def handle_photo_note_voice(message: Message, state: FSMContext) -> None:
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
        await message.answer("❌ Не удалось распознать голос.")
        return
    await message.answer(f"🎤 Распознано: <i>{text}</i>", parse_mode="HTML")
    await _save_photo_note(state, message, text)


async def _save_photo_note(state: FSMContext, message: Message, text: str) -> None:
    data = await state.get_data()
    pid = data.get("last_photo_id")
    if not pid:
        await message.answer("Не нашёл фото.", reply_markup=main_menu())
        await state.clear()
        return
    await update_photo_notes(pid, text.strip())
    await state.clear()
    await message.answer(
        f"✅ Коммент сохранён: <i>{text.strip()}</i>",
        parse_mode="HTML",
        reply_markup=photos_menu(has_history=True),
    )


@router.callback_query(F.data == "photo:skip_note", PhotoStates.waiting_note)
async def cb_photo_skip_note(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Без комментария.", reply_markup=photos_menu(has_history=True))


@router.callback_query(F.data == "photo:delete_last", PhotoStates.waiting_note)
async def cb_photo_delete_last(cb: CallbackQuery, state: FSMContext) -> None:
    """Delete the WHOLE series (all photos sharing the same series_id)."""
    await cb.answer()
    data = await state.get_data()
    series_id = data.get("photo_series_id")
    if series_id:
        from sqlalchemy import text
        from app.db.engine import get_session
        async with get_session() as s:
            await s.execute(
                text("DELETE FROM progress_photos WHERE series_id = :sid"),
                {"sid": series_id},
            )
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("🗑 Серия удалена.", reply_markup=photos_menu(has_history=False))


# ───────────────────────── history ───────────────────────────────────────

@router.callback_query(F.data == "photo:history")
async def cb_photo_history(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    uid = str(cb.from_user.id)
    rows = await get_photos(uid, limit=60)
    if not rows:
        await cb.message.edit_text("📭 Нет фото.", reply_markup=photos_menu(has_history=False))
        return
    await state.update_data(photo_ids=[r["id"] for r in rows], photo_idx=0)
    await _show_photo(cb.message, rows[0], idx=0, total=len(rows), edit=True)


@router.callback_query(F.data.startswith("photo:nav:"))
async def cb_photo_nav(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    idx = int(cb.data.split(":")[-1])
    data = await state.get_data()
    photo_ids: list[int] = data.get("photo_ids", [])
    if not photo_ids or idx < 0 or idx >= len(photo_ids):
        return
    p = await get_photo(photo_ids[idx])
    if not p:
        await cb.message.answer("Фото не найдено")
        return
    await state.update_data(photo_idx=idx)
    await _show_photo(cb.message, p, idx=idx, total=len(photo_ids), edit=False)


@router.callback_query(F.data == "photo:back")
async def cb_photo_back(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    uid = str(cb.from_user.id)
    photos = await get_photos(uid, limit=1)
    await state.clear()
    await cb.message.answer(
        "📸 Прогресс-фото",
        reply_markup=photos_menu(has_history=bool(photos)),
    )


@router.callback_query(F.data.startswith("photo:delete:"))
async def cb_photo_delete(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    pid = int(cb.data.split(":")[-1])
    await delete_photo(pid)
    data = await state.get_data()
    photo_ids: list[int] = [x for x in data.get("photo_ids", []) if x != pid]
    if not photo_ids:
        await state.clear()
        await cb.message.answer("🗑 Удалено. Больше фото нет.",
                                reply_markup=photos_menu(has_history=False))
        return
    await state.update_data(photo_ids=photo_ids, photo_idx=0)
    p = await get_photo(photo_ids[0])
    if p:
        await _show_photo(cb.message, p, idx=0, total=len(photo_ids), edit=False)


@router.callback_query(F.data == "photo:noop")
async def cb_photo_noop(cb: CallbackQuery) -> None:
    await cb.answer()


async def _show_photo(message: Message, p: dict, idx: int, total: int, edit: bool) -> None:
    caption_lines = [f"📅 <b>{p['taken_on']}</b>"]
    if p.get("ai_description"):
        caption_lines.append(p["ai_description"][:850])
    if p.get("notes"):
        caption_lines.append(f"💬 {p['notes']}")
    caption = "\n\n".join(caption_lines)
    if len(caption) > 1000:
        caption = caption[:1000] + "..."
    try:
        await message.answer_photo(
            photo=p["telegram_file_id"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=photo_nav(p["id"], idx, total),
        )
    except Exception as exc:
        log.exception("send photo failed: %s", exc)
        await message.answer(
            f"❌ Не удалось показать фото: {exc}",
            reply_markup=photos_menu(has_history=True),
        )
