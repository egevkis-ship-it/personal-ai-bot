"""
Plan management FSM handler.

Flows:
  A. Load plan via text paste → AI parse → confirm → save to DB
  B. Load plan manually (day by day) — basic flow
  C. View plan (browse by day with pagination)
  D. Start workout from a plan day
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    back_button,
    plan_confirm_parsed,
    plan_day_actions,
    plan_load_mode,
    plan_menu,
    plan_navigate,
)
from app.bot.services.ai_parser import PlannedDay, parse_plan_text
from app.bot.services.formatter import format_planned_day, format_weekly_plan
from app.bot.states import PlanStates, WorkoutStates
from app.db import (
    create_planned_workout,
    create_workout,
    get_planned_workout,
    get_planned_workouts_range,
    delete_planned_workout,
)

log = logging.getLogger(__name__)
router = Router(name="plans")

_WEEKDAY_ORDER = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _exercises_to_db(exercises) -> list[dict]:
    result = []
    for e in exercises:
        d = {
            "name": e.name,
            "target_sets": e.target_sets,
            "target_reps_min": e.target_reps_min,
            "target_reps_max": e.target_reps_max,
            "target_weight": e.target_weight,
            "reps_text": e.reps_text,
            "notes": e.notes,
            "superset_group": e.superset_group,
        }
        result.append(d)
    return result


def _assign_dates(days: list[PlannedDay]) -> list[tuple[PlannedDay, date]]:
    """
    Assign real dates to parsed days.
    Logic:
    - If day_label looks like a weekday name → find next occurrence from today
    - If day_label looks like ISO date → parse directly
    - Otherwise: today + index
    """
    today = date.today()
    result = []
    for i, day in enumerate(days):
        label_lower = day.day_label.lower().strip()
        # Try weekday match
        matched_weekday = None
        for wd_i, wd_name in enumerate(_WEEKDAY_ORDER):
            if wd_name.lower() in label_lower:
                matched_weekday = wd_i
                break
        if matched_weekday is not None:
            days_ahead = (matched_weekday - today.weekday()) % 7
            if days_ahead == 0 and i > 0:
                days_ahead = 7  # avoid stacking on same day
            d = today + timedelta(days=days_ahead)
        else:
            # Try ISO date
            try:
                d = date.fromisoformat(day.day_label.strip())
            except ValueError:
                d = today + timedelta(days=i)
        result.append((day, d))
    return result


# ────────────────────────── plan menu entry ──────────────────────────────────

@router.callback_query(F.data == "plan:load")
async def cb_plan_load(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await state.set_state(PlanStates.choose_load_mode)
    await cb.message.edit_text(
        "📥 Как загрузить план?",
        reply_markup=plan_load_mode(),
    )


# ────────────────────────── load via text paste ──────────────────────────────

@router.callback_query(F.data == "plan_load:paste", PlanStates.choose_load_mode)
async def cb_paste_mode(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PlanStates.paste_text)
    await cb.message.edit_text(
        "📝 Отправь текст плана.\n\n"
        "Например:\n"
        "<i>Понедельник — Грудь:\n"
        "Жим лёжа 4×8-12 80кг\n"
        "Разводка 3×12\n\n"
        "Среда — Спина:\n"
        "Тяга 4×8 90кг\n"
        "Подтягивания 4×max</i>",
        parse_mode="HTML",
        reply_markup=back_button("back:plan"),
    )
    await cb.answer()


@router.message(PlanStates.paste_text, F.text)
async def handle_plan_paste(message: Message, state: FSMContext) -> None:
    await message.answer("⏳ Разбираю план...")
    days = await parse_plan_text(message.text)
    if not days:
        await message.answer(
            "❌ Не удалось разобрать план. Попробуй другой формат или введи вручную.",
            reply_markup=plan_load_mode(),
        )
        await state.set_state(PlanStates.choose_load_mode)
        return

    # Store parsed days in state
    days_data = [
        {
            "day_label": d.day_label,
            "focus_label": d.focus_label,
            "exercises": _exercises_to_db(d.exercises),
        }
        for d in days
    ]
    await state.update_data(parsed_days=days_data)
    await state.set_state(PlanStates.confirm_parsed)

    # Build preview
    preview_lines = []
    for d in days[:7]:  # show max 7 days in preview
        focus = d.focus_label or "тренировка"
        n_ex = len(d.exercises)
        preview_lines.append(f"  • {d.day_label} — {focus} ({n_ex} упр.)")
    if len(days) > 7:
        preview_lines.append(f"  ... ещё {len(days) - 7} дн.")

    preview = "\n".join(preview_lines)
    await message.answer(
        f"✅ Разобрано {len(days)} дней:\n{preview}",
        reply_markup=plan_confirm_parsed(len(days)),
    )


@router.callback_query(F.data == "plan_load:confirm")
async def cb_confirm_parsed(cb: CallbackQuery, state: FSMContext) -> None:
    # Answer immediately so Telegram stops showing spinner
    await cb.answer()

    try:
        data = await state.get_data()
        days_data: list[dict] = data.get("parsed_days", [])

        if not days_data:
            await cb.message.answer(
                "❌ Данные плана потеряны (сессия устарела). Загрузи план заново.",
                reply_markup=plan_menu(),
            )
            await state.clear()
            return

        uid = str(cb.from_user.id)

        from app.bot.services.ai_parser import PlannedDay
        from app.bot.services.exercise_catalog import resolve_or_register
        import asyncio as _asyncio

        days = [
            PlannedDay(
                day_label=d["day_label"],
                focus_label=d.get("focus_label"),
                exercises=[],
            )
            for d in days_data
        ]
        assignments = _assign_dates(days)

        # Normalize exercise names through the catalog (parallel per day).
        # Failures fall back to the raw name — never block plan saving.
        async def _normalize_day(day_dict: dict) -> list[dict]:
            exs = day_dict.get("exercises", []) or []
            if not exs:
                return []
            resolved = await _asyncio.gather(
                *[resolve_or_register(e.get("name") or "") for e in exs],
                return_exceptions=True,
            )
            out = []
            for ex, r in zip(exs, resolved):
                new_ex = dict(ex)
                if isinstance(r, dict) and r.get("canonical"):
                    new_ex["name"] = r["canonical"]
                    new_ex["muscle_group"] = r.get("muscle_group")
                out.append(new_ex)
            return out

        normalized_per_day = await _asyncio.gather(
            *[_normalize_day(dd) for dd in days_data],
            return_exceptions=False,
        )

        saved = 0
        for (day, assigned_date), day_dict, norm_exs in zip(
            assignments, days_data, normalized_per_day
        ):
            await create_planned_workout(
                user_id=uid,
                planned_date=assigned_date,
                focus_label=day_dict.get("focus_label"),
                exercises=norm_exs,
            )
            saved += 1

        await state.clear()
        await cb.message.answer(
            f"✅ План сохранён! {saved} дней добавлено в расписание.",
            reply_markup=plan_menu(),
        )

    except Exception as e:
        log.exception("cb_confirm_parsed error")
        import html as _html
        await cb.message.answer(
            f"❌ Ошибка при сохранении:\n<code>{_html.escape(str(e))[:400]}</code>",
            parse_mode="HTML",
            reply_markup=plan_menu(),
        )
        await state.clear()


@router.callback_query(F.data == "plan_load:remap", PlanStates.confirm_parsed)
async def cb_remap_dates(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.answer(
        "✏️ Функция ручного переназначения дат пока в разработке.\n"
        "Нажми Сохранить — даты подставятся автоматически по дням недели.",
        reply_markup=plan_confirm_parsed(1),
    )
    await cb.answer()


@router.callback_query(F.data == "plan_load:cancel")
async def cb_cancel_load(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("❌ Загрузка отменена.", reply_markup=plan_menu())
    await cb.answer()


# ────────────────────────── view plan ────────────────────────────────────────

@router.callback_query(F.data == "plan:view")
async def cb_view_plan(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    uid = str(cb.from_user.id)
    today = date.today()
    # Show next 30 days
    plans = await get_planned_workouts_range(uid, today, today + timedelta(days=30))
    if not plans:
        await cb.message.edit_text(
            "📭 Нет запланированных тренировок на ближайшие 30 дней.",
            reply_markup=plan_menu(),
        )
        return

    await state.set_state(PlanStates.browsing)
    await state.update_data(plan_ids=[p["id"] for p in plans], plan_idx=0)

    text = format_planned_day(plans[0])
    await cb.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=plan_navigate(plans[0]["id"], 0, len(plans)),
    )


@router.callback_query(F.data.startswith("plan:nav:"), PlanStates.browsing)
async def cb_plan_nav(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    idx = int(cb.data.split(":")[-1])
    data = await state.get_data()
    plan_ids: list[int] = data.get("plan_ids", [])
    if idx < 0 or idx >= len(plan_ids):
        return
    plan = await get_planned_workout(plan_ids[idx])
    if not plan:
        await cb.message.answer("День не найден")
        return
    await state.update_data(plan_idx=idx)
    text = format_planned_day(plan)
    await cb.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=plan_navigate(plan["id"], idx, len(plan_ids)),
    )


@router.callback_query(F.data == "plan:noop")
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()


# ────────────────────────── start from plan ──────────────────────────────────

@router.callback_query(F.data.startswith("plan:start:"))
async def cb_start_from_plan(cb: CallbackQuery, state: FSMContext) -> None:
    plan_id = int(cb.data.split(":")[-1])
    plan = await get_planned_workout(plan_id)
    if not plan:
        await cb.answer("План не найден", show_alert=True)
        return

    uid = str(cb.from_user.id)
    workout_id = await create_workout(
        user_id=uid,
        workout_date=date.today(),
        focus_label=plan.get("focus_label"),
        planned_workout_id=plan_id,
    )
    await state.update_data(workout_id=workout_id, last_exercise=None)
    await state.set_state(WorkoutStates.active)

    from app.bot.keyboards import workout_active_menu
    focus = plan.get("focus_label") or "тренировка"
    # Show planned exercises as a reminder
    exercises: list[dict] = plan.get("exercises") or []
    plan_text = ""
    if exercises:
        lines = []
        for ex in exercises[:8]:
            name = ex.get("name", "?")
            sets = ex.get("target_sets") or "?"
            r_min = ex.get("target_reps_min")
            r_max = ex.get("target_reps_max")
            if r_min and r_max and r_min != r_max:
                reps_str = f"{r_min}–{r_max}"
            elif r_min:
                reps_str = str(r_min)
            else:
                reps_str = "?"
            w = ex.get("target_weight")
            w_str = f" · {w:g} кг" if w else ""
            lines.append(f"  • {name}: {sets}×{reps_str}{w_str}")
        plan_text = "\n\nПлан на сегодня:\n" + "\n".join(lines)

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        f"🏋️ Начата тренировка: <b>{focus}</b>{plan_text}\n\n"
        "Отправляй подходы:",
        parse_mode="HTML",
        reply_markup=workout_active_menu(),
    )
    await cb.answer()


# ────────────────────────── delete plan day ──────────────────────────────────

@router.callback_query(F.data.startswith("plan:delete:"), PlanStates.browsing)
async def cb_delete_plan(cb: CallbackQuery, state: FSMContext) -> None:
    plan_id = int(cb.data.split(":")[-1])
    await delete_planned_workout(plan_id)
    data = await state.get_data()
    plan_ids: list[int] = data.get("plan_ids", [])
    plan_ids = [p for p in plan_ids if p != plan_id]
    if not plan_ids:
        await state.clear()
        await cb.message.edit_text("🗑 День удалён. Нет больше запланированных тренировок.", reply_markup=plan_menu())
        await cb.answer()
        return
    await state.update_data(plan_ids=plan_ids, plan_idx=0)
    plan = await get_planned_workout(plan_ids[0])
    text = format_planned_day(plan)
    await cb.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=plan_navigate(plan_ids[0], 0, len(plan_ids)),
    )
    await cb.answer("Удалено ✅")
