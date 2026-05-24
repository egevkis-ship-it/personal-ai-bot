"""
Service / maintenance menu. Wipes, stats, AI cache reset.

Every destructive op is two-step:
  1) user clicks the option → confirmation prompt shown
  2) user clicks "Да, выполнить" → action runs
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    confirm_destructive,
    main_menu,
    service_menu,
)
from app.db import (
    db_stats,
    wipe_all_user_data,
    wipe_exercise_aliases,
    wipe_measurements,
    wipe_photos,
    wipe_planned_workouts,
    wipe_workouts,
)

log = logging.getLogger(__name__)
router = Router(name="service")


_CONFIRM_TEXTS = {
    "wipe_plans": (
        "🗑 <b>Удалить ВСЕ запланированные тренировки?</b>\n"
        "Это удалит и активные, и пропущенные планы. Действие необратимо."
    ),
    "wipe_history": (
        "🗑 <b>Удалить ВСЮ историю тренировок?</b>\n"
        "Все записанные подходы и сессии будут удалены. Действие необратимо."
    ),
    "wipe_measurements": (
        "🗑 <b>Удалить ВСЕ замеры тела?</b>\n"
        "Все сохранённые замеры (вес, обхваты) будут удалены. Действие необратимо."
    ),
    "wipe_photos": (
        "🗑 <b>Удалить ВСЕ прогресс-фото?</b>\n"
        "Записи в дневнике (file_id и AI-описания) будут удалены. "
        "Сами файлы остаются в Telegram. Действие необратимо."
    ),
    "wipe_aliases": (
        "🧹 <b>Очистить кэш AI-нормализации?</b>\n"
        "Сохранённые AI-разрешения имён упражнений будут удалены. "
        "В следующий раз каждое неизвестное название снова спросит у AI."
    ),
    "wipe_all": (
        "⚠️ <b>ПОЛНЫЙ СБРОС</b>\n"
        "Удалит ВСЁ: планы, тренировки, замеры, фото. Необратимо. Точно?"
    ),
}


# ───────────────────────── entry ─────────────────────────────────────────────

@router.message(F.text == "⚙️ Сервис")
async def menu_service(message: Message, state: FSMContext) -> None:
    await state.clear()
    stats = await db_stats(str(message.from_user.id))
    await message.answer(
        _format_intro(stats),
        parse_mode="HTML",
        reply_markup=service_menu(),
    )


def _format_intro(stats: dict) -> str:
    return (
        "⚙️ <b>Сервис</b>\n\n"
        f"📅 Планов активных: <b>{stats.get('planned_active', 0)}</b> "
        f"(всего: {stats.get('planned_total', 0)})\n"
        f"🏋️ Тренировок: <b>{stats.get('workouts_finished', 0)}</b> завершённых "
        f"(всего: {stats.get('workouts_total', 0)})\n"
        f"📝 Подходов: <b>{stats.get('sets_total', 0)}</b>\n"
        f"📏 Замеров: <b>{stats.get('measurements_total', 0)}</b>\n"
        f"📸 Фото: <b>{stats.get('photos_total', 0)}</b>\n"
        f"🤖 AI-алиасов: <b>{stats.get('aliases_total', 0)}</b>"
    )


@router.callback_query(F.data == "svc:back")
async def cb_svc_back(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    stats = await db_stats(str(cb.from_user.id))
    await cb.message.edit_text(
        _format_intro(stats),
        parse_mode="HTML",
        reply_markup=service_menu(),
    )


@router.callback_query(F.data == "svc:stats")
async def cb_svc_stats(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    stats = await db_stats(str(cb.from_user.id))
    await cb.message.edit_text(
        _format_intro(stats),
        parse_mode="HTML",
        reply_markup=service_menu(),
    )


# ───────────────────────── confirm-then-act ──────────────────────────────────

@router.callback_query(F.data.in_({
    "svc:wipe_plans", "svc:wipe_history",
    "svc:wipe_measurements", "svc:wipe_photos",
    "svc:wipe_aliases", "svc:wipe_all",
}))
async def cb_svc_ask(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    action = cb.data.split(":", 1)[1]  # wipe_plans / wipe_history / ...
    prompt = _CONFIRM_TEXTS.get(action, "Точно?")
    await cb.message.edit_text(prompt, parse_mode="HTML",
                               reply_markup=confirm_destructive(action))


@router.callback_query(F.data.startswith("svc:do:"))
async def cb_svc_do(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    action = cb.data.split(":", 2)[2]
    uid = str(cb.from_user.id)
    try:
        if action == "wipe_plans":
            n = await wipe_planned_workouts(uid)
            msg = f"✅ Удалено запланированных тренировок: <b>{n}</b>"
        elif action == "wipe_history":
            n = await wipe_workouts(uid)
            msg = f"✅ Удалено тренировок: <b>{n}</b> (подходы каскадом)"
        elif action == "wipe_measurements":
            n = await wipe_measurements(uid)
            msg = f"✅ Удалено замеров: <b>{n}</b>"
        elif action == "wipe_photos":
            n = await wipe_photos(uid)
            msg = f"✅ Удалено записей о фото: <b>{n}</b>"
        elif action == "wipe_aliases":
            n = await wipe_exercise_aliases()
            msg = f"✅ Очищено AI-алиасов: <b>{n}</b>"
        elif action == "wipe_all":
            res = await wipe_all_user_data(uid)
            msg = (
                "✅ Полный сброс выполнен:\n"
                f"  планов: <b>{res['planned']}</b>\n"
                f"  тренировок: <b>{res['workouts']}</b>\n"
                f"  замеров: <b>{res['measurements']}</b>\n"
                f"  фото: <b>{res['photos']}</b>"
            )
        else:
            msg = f"❌ Неизвестное действие: {action}"
    except Exception as exc:
        log.exception("service action %s failed", action)
        import html as _html
        msg = f"❌ Ошибка: <code>{_html.escape(str(exc))[:300]}</code>"

    stats = await db_stats(uid)
    await cb.message.edit_text(
        f"{msg}\n\n{_format_intro(stats)}",
        parse_mode="HTML",
        reply_markup=service_menu(),
    )
