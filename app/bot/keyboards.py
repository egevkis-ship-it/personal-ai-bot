"""
All inline and reply keyboards for the fitness bot.
Convention: functions return InlineKeyboardMarkup or ReplyKeyboardMarkup.
Callback data format: "action:payload" (keep payloads short).
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


# ─────────────────────────── main menu ───────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.button(text="💪 Тренировка")
    kb.button(text="📅 Планы")
    kb.button(text="📖 История")
    kb.button(text="⚙️ Сервис")
    kb.adjust(3, 1)
    return kb.as_markup(resize_keyboard=True)


def service_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика БД", callback_data="svc:stats")
    kb.button(text="🗑 Очистить запланированные", callback_data="svc:wipe_plans")
    kb.button(text="🗑 Очистить историю", callback_data="svc:wipe_history")
    kb.button(text="🧹 Очистить AI-кэш упражнений", callback_data="svc:wipe_aliases")
    kb.button(text="⚠️ ПОЛНЫЙ СБРОС (всё)", callback_data="svc:wipe_all")
    kb.button(text="🔙 Назад", callback_data="back:main")
    kb.adjust(1)
    return kb.as_markup()


def confirm_destructive(action: str) -> InlineKeyboardMarkup:
    """Two-step confirm. action goes as final callback data 'svc:do:<action>'."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, выполнить", callback_data=f"svc:do:{action}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="svc:back"),
    ]])


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


# ─────────────────────────── generic ─────────────────────────────────────────

def yes_no(yes_cb: str, no_cb: str, yes_label: str = "✅ Да", no_label: str = "❌ Отмена") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=yes_label, callback_data=yes_cb),
        InlineKeyboardButton(text=no_label, callback_data=no_cb),
    ]])


def back_button(cb: str = "back:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔙 Назад", callback_data=cb),
    ]])


# ─────────────────────────── workout ─────────────────────────────────────────

def workout_start(has_plan: bool) -> InlineKeyboardMarkup:
    """Shown when user taps 💪 Тренировка."""
    kb = InlineKeyboardBuilder()
    if has_plan:
        kb.button(text="▶️ Начать по плану", callback_data="workout:start_planned")
    kb.button(text="🆕 Свободная тренировка", callback_data="workout:start_free")
    kb.button(text="🔙 Назад", callback_data="back:main")
    kb.adjust(1)
    return kb.as_markup()


def workout_active_menu() -> InlineKeyboardMarkup:
    """Persistent inline menu during active workout."""
    kb = InlineKeyboardBuilder()
    kb.button(text="👁 Показать сессию", callback_data="workout:show")
    kb.button(text="✏️ Коммент к последнему", callback_data="workout:note_last")
    kb.button(text="↩️ Удалить последний", callback_data="workout:delete_last")
    kb.button(text="🏁 Завершить тренировку", callback_data="workout:finish")
    kb.adjust(1)
    return kb.as_markup()


def skip_or_cancel(skip_cb: str = "note:skip", cancel_cb: str = "note:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=skip_cb),
        InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb),
    ]])


def post_finish_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🤖 AI-разбор тренировки", callback_data="finish:coach")
    kb.button(text="📅 Запланировать новую", callback_data="finish:plan_next")
    kb.button(text="✅ Готово", callback_data="finish:done")
    kb.adjust(1)
    return kb.as_markup()


def plan_next_mode() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Вручную", callback_data="plan_next:manual")
    kb.button(text="🤖 Через AI", callback_data="plan_next:ai")
    kb.button(text="🔙 Назад", callback_data="finish:done")
    kb.adjust(1)
    return kb.as_markup()


def ai_plan_period() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📆 1 день", callback_data="ai_plan:day")
    kb.button(text="🗓 Неделя", callback_data="ai_plan:week")
    kb.button(text="🔙 Назад", callback_data="finish:plan_next")
    kb.adjust(1)
    return kb.as_markup()


def ai_plan_day_picker(days: list[tuple[str, str, str | None]]) -> InlineKeyboardMarkup:
    """days = list of (iso_date, label, busy_with_focus_or_None)."""
    kb = InlineKeyboardBuilder()
    for iso, label, busy in days:
        prefix = "🔒 " if busy else ""
        suffix = f" ({busy})" if busy else ""
        kb.button(text=f"{prefix}{label}{suffix}", callback_data=f"ai_plan:date:{iso}")
    kb.button(text="🔙 Назад", callback_data="finish:plan_next")
    kb.adjust(1)
    return kb.as_markup()


def ai_plan_confirm(date_iso: str, replace: bool = False) -> InlineKeyboardMarkup:
    """Confirm-or-discard for a generated single-day plan.
    `replace=True` adds the explicit replace button when the day was busy.
    """
    kb = InlineKeyboardBuilder()
    if replace:
        kb.button(text="🔁 Заменить существующую", callback_data=f"ai_plan:save_replace:{date_iso}")
    else:
        kb.button(text="✅ Сохранить", callback_data=f"ai_plan:save:{date_iso}")
    kb.button(text="🔄 Другой день", callback_data="ai_plan:day")
    kb.button(text="❌ Отмена", callback_data="finish:done")
    kb.adjust(1)
    return kb.as_markup()


def ai_plan_week_confirm() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Сохранить неделю", callback_data="ai_plan:save_week")
    kb.button(text="❌ Отмена", callback_data="finish:done")
    kb.adjust(1)
    return kb.as_markup()


def occupied_day_choice(date_iso: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔁 Заменить", callback_data=f"ai_plan:gen:{date_iso}:replace")
    kb.button(text="📆 Другой день", callback_data="ai_plan:day")
    kb.button(text="❌ Отмена", callback_data="finish:done")
    kb.adjust(1)
    return kb.as_markup()


def confirm_sets(sets_summary: str) -> InlineKeyboardMarkup:
    """Confirm or discard parsed sets."""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Записать", callback_data="set:confirm")
    kb.button(text="✏️ Исправить", callback_data="set:edit")
    kb.button(text="❌ Отмена", callback_data="set:cancel")
    kb.adjust(3)
    return kb.as_markup()


def finish_workout_confirm() -> InlineKeyboardMarkup:
    return yes_no("workout:finish_yes", "workout:finish_no",
                  yes_label="🏁 Завершить", no_label="↩️ Продолжить")


# ─────────────────────────── plans ───────────────────────────────────────────

def plan_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📥 Загрузить план", callback_data="plan:load")
    kb.button(text="📋 Посмотреть план", callback_data="plan:view")
    kb.button(text="🔙 Назад", callback_data="back:main")
    kb.adjust(1)
    return kb.as_markup()


def plan_load_mode() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Вставить текст", callback_data="plan_load:paste")
    kb.button(text="✍️ Ввести вручную", callback_data="plan_load:manual")
    kb.button(text="🔙 Назад", callback_data="back:plan")
    kb.adjust(1)
    return kb.as_markup()


def plan_confirm_parsed(n_days: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"✅ Сохранить ({n_days} дн.)", callback_data="plan_load:confirm")
    kb.button(text="✏️ Изменить даты", callback_data="plan_load:remap")
    kb.button(text="❌ Отмена", callback_data="plan_load:cancel")
    kb.adjust(1)
    return kb.as_markup()


def plan_day_actions(plan_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Начать тренировку", callback_data=f"plan:start:{plan_id}")
    kb.button(text="🗑 Удалить день", callback_data=f"plan:delete:{plan_id}")
    kb.button(text="🔙 Назад", callback_data="plan:view")
    kb.adjust(1)
    return kb.as_markup()


def plan_navigate(plan_id: int, idx: int, total: int) -> InlineKeyboardMarkup:
    """Pagination for browsing plan days."""
    kb = InlineKeyboardBuilder()
    if idx > 0:
        kb.button(text="◀️", callback_data=f"plan:nav:{idx-1}")
    kb.button(text=f"{idx+1}/{total}", callback_data="plan:noop")
    if idx < total - 1:
        kb.button(text="▶️", callback_data=f"plan:nav:{idx+1}")
    kb.adjust(3)
    kb.row(
        InlineKeyboardButton(text="▶️ Начать", callback_data=f"plan:start:{plan_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"plan:delete:{plan_id}"),
    )
    kb.row(InlineKeyboardButton(text="🔙 К меню планов", callback_data="back:plan"))
    return kb.as_markup()


# ─────────────────────────── history ─────────────────────────────────────────

def history_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Сегодня", callback_data="hist:today")
    kb.button(text="🗓 Эта неделя", callback_data="hist:week")
    kb.button(text="📆 Этот месяц", callback_data="hist:month")
    kb.button(text="📤 Экспорт", callback_data="hist:export")
    kb.button(text="🔙 Назад", callback_data="back:main")
    kb.adjust(2)
    return kb.as_markup()


def export_format() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Текст", callback_data="export:text")
    kb.button(text="📊 CSV", callback_data="export:csv")
    kb.button(text="🔙 Назад", callback_data="back:history")
    kb.adjust(2)
    return kb.as_markup()


def export_period() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="7 дней", callback_data="export_period:7")
    kb.button(text="30 дней", callback_data="export_period:30")
    kb.button(text="Всё время", callback_data="export_period:all")
    kb.adjust(3)
    return kb.as_markup()
