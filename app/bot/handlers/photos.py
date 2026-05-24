"""
Progress photos handler.

Flow:
  /Фото → menu (New | History | Back)
  New → waiting_photo → user sends photo
       → bot downloads → AI Vision describes (1× call, stored)
       → waiting_note → optional text/voice note → save
  History → list of photos with descriptions, paginated
"""
from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    main_menu,
    photo_after_upload,
    photo_nav,
    photos_menu,
)
from app.bot.services.ai_parser import transcribe_voice
from app.bot.services.photo_vision import describe_photo
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
            intro += f"\n<i>{last['ai_description'][:200]}</i>"
    await message.answer(
        intro, parse_mode="HTML",
        reply_markup=photos_menu(has_history=bool(photos)),
    )


# ─────────────────────────── new photo flow ─────────────────────────────────

@router.callback_query(F.data == "photo:new")
async def cb_photo_new(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await state.set_state(PhotoStates.waiting_photo)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "📷 Отправь фото для прогресса. Можно несколько подряд.\n"
        "Я сразу попрошу AI кратко описать что видно — это сохранится "
        "в дневник для будущих сравнений."
    )


@router.message(PhotoStates.waiting_photo, F.photo)
async def handle_photo_upload(message: Message, state: FSMContext) -> None:
    placeholder = await message.answer("🧠 Получил фото, описываю через AI…")
    try:
        # photo[-1] = highest resolution
        photo = message.photo[-1]
        bot = message.bot
        file = await bot.get_file(photo.file_id)
        file_io = await bot.download_file(file.file_path)
        img_bytes = file_io.read() if hasattr(file_io, "read") else bytes(file_io)
    except Exception as exc:
        log.exception("photo download error")
        await placeholder.edit_text(f"❌ Не удалось скачать фото: {exc}")
        return

    description = await describe_photo(img_bytes, media_type="image/jpeg")
    uid = str(message.from_user.id)
    try:
        photo_id = await create_photo(
            user_id=uid,
            taken_on=date.today(),
            telegram_file_id=photo.file_id,
            telegram_file_unique_id=photo.file_unique_id,
            ai_description=description,
        )
    except Exception as exc:
        log.exception("save photo failed")
        await placeholder.edit_text(f"❌ Не сохранилось: {exc}")
        return

    await state.update_data(last_photo_id=photo_id)
    await state.set_state(PhotoStates.waiting_note)

    desc_text = description or "<i>(описание не получено)</i>"
    try:
        await placeholder.edit_text(
            f"✅ Сохранено.\n\n<b>AI-описание:</b>\n{desc_text}\n\n"
            "Можешь дописать свой комментарий (текст или голос), либо пропустить.",
            parse_mode="HTML",
            reply_markup=photo_after_upload(),
        )
    except Exception:
        await message.answer(
            f"✅ Сохранено.\n\n<b>AI-описание:</b>\n{desc_text}\n\n"
            "Можешь дописать свой комментарий (текст или голос), либо пропустить.",
            parse_mode="HTML",
            reply_markup=photo_after_upload(),
        )


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
    await cb.answer()
    data = await state.get_data()
    pid = data.get("last_photo_id")
    if pid:
        await delete_photo(pid)
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("🗑 Фото удалено.", reply_markup=photos_menu(has_history=False))


# ─────────────────────────── history view ──────────────────────────────────

@router.callback_query(F.data == "photo:history")
async def cb_photo_history(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    uid = str(cb.from_user.id)
    rows = await get_photos(uid, limit=30)
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
        caption_lines.append(f"<i>{p['ai_description'][:700]}</i>")
    if p.get("notes"):
        caption_lines.append(f"💬 {p['notes']}")
    caption = "\n\n".join(caption_lines)
    # Telegram caption limit = 1024
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
