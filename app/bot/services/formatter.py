"""
Text formatters for bot messages.
All functions return plain Telegram-compatible strings (MarkdownV2 safe via HTML parse_mode).
Use parse_mode=HTML in all sends.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


# ──────────────────────────── helpers ────────────────────────────────────────

_DAYS_RU = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье",
}
_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _esc(s: str) -> str:
    """Escape HTML special chars."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_date(d: date | str) -> str:
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d)
        except ValueError:
            return d
    day_name = _DAYS_RU.get(d.weekday(), "")
    return f"{day_name}, {d.day} {_MONTHS_RU[d.month]}"


def fmt_weight(w: float | None) -> str:
    if w is None:
        return "—"
    return f"{w:g} кг"


def fmt_duration(secs: int | None) -> str:
    if secs is None:
        return "—"
    m, s = divmod(secs, 60)
    return f"{m}:{s:02d}" if m else f"{s} сек"


def fmt_reps(reps: int | None, reps_text: str | None, is_failure: bool) -> str:
    if is_failure:
        base = f"{reps}" if reps else ""
        return f"{base} (до отказа)".strip()
    if reps_text:
        return reps_text
    return str(reps) if reps else "—"


# ──────────────────────────── plan formatters ────────────────────────────────

def format_planned_day(plan: dict[str, Any]) -> str:
    """Format one planned_workouts row for display."""
    d = plan.get("planned_date")
    focus = plan.get("focus_label") or "тренировка"
    exercises: list[dict] = plan.get("exercises") or []
    plan_notes = plan.get("notes")

    lines = [f"📅 <b>{fmt_date(d)} — {_esc(focus)}</b>"]
    if plan_notes:
        lines.append(f"💬 <i>{_esc(plan_notes)}</i>")
    if not exercises:
        lines.append("  <i>упражнения не добавлены</i>")
    else:
        for ex in exercises:
            name = _esc(ex.get("name", "?"))
            sets = ex.get("target_sets") or "?"
            r_min = ex.get("target_reps_min")
            r_max = ex.get("target_reps_max")
            rt = ex.get("reps_text")
            w = ex.get("target_weight")
            ex_notes = ex.get("notes")

            if rt:
                reps_str = rt
            elif r_min and r_max and r_min != r_max:
                reps_str = f"{r_min}–{r_max}"
            elif r_min:
                reps_str = str(r_min)
            else:
                reps_str = "?"

            w_str = f" · {w:g} кг" if w else ""
            sg = f" [{ex.get('superset_group')}]" if ex.get("superset_group") else ""
            lines.append(f"  • {name}{sg} — {sets}×{reps_str}{w_str}")
            if ex_notes:
                lines.append(f"    <i>💬 {_esc(ex_notes)}</i>")
    return "\n".join(lines)


def format_weekly_plan(plans: list[dict[str, Any]]) -> str:
    """Format multiple planned days (a week or period)."""
    if not plans:
        return "📭 Нет запланированных тренировок."
    parts = [format_planned_day(p) for p in plans]
    return "\n\n".join(parts)


# ──────────────────────────── workout formatters ─────────────────────────────

def format_set_line(s: dict[str, Any], idx: int) -> str:
    """Single set as a compact line: '3. Жим — 80 кг × 10'"""
    name = _esc(s.get("exercise_name", "?"))
    w = s.get("weight_kg")
    reps = s.get("reps")
    rt = s.get("reps_text")
    dur = s.get("duration_seconds")
    fail = s.get("is_failure", False)
    warmup = s.get("is_warmup", False)
    sg = s.get("superset_group")

    prefix = f"{idx}."
    if warmup:
        prefix += " 🔥"
    if sg:
        prefix += f" [{sg}]"

    if dur:
        result = f"{prefix} {name} — {fmt_duration(dur)}"
    elif w is not None:
        reps_str = fmt_reps(reps, rt, fail)
        result = f"{prefix} {name} — {w:g} кг × {reps_str}"
    else:
        reps_str = fmt_reps(reps, rt, fail)
        result = f"{prefix} {name} — {reps_str}"
    return result


def _fmt_time(dt_val, zone=None) -> str:
    """Format a stored UTC timestamp as HH:MM in the user's zone."""
    if dt_val is None:
        return "?"
    if isinstance(dt_val, str):
        try:
            dt_val = datetime.fromisoformat(dt_val)
        except Exception:
            return dt_val
    if not isinstance(dt_val, datetime):
        return "?"
    if zone is not None:
        from app.bot.services.tz import to_local
        dt_val = to_local(dt_val, zone)
    return dt_val.strftime("%H:%M")


def format_active_session(sets: list[dict[str, Any]], workout: dict[str, Any], zone=None) -> str:
    """Full active workout display."""
    focus = workout.get("focus_label") or "тренировка"
    time_str = _fmt_time(workout.get("started_at"), zone)

    lines = [f"💪 <b>{_esc(focus)}</b> (с {time_str})\n"]

    if not sets:
        lines.append("<i>подходы ещё не записаны</i>")
        return "\n".join(lines)

    # Group by exercise, preserve order
    seen: list[str] = []
    by_ex: dict[str, list[dict]] = {}
    for s in sets:
        ex = s.get("exercise_name", "?")
        if ex not in by_ex:
            by_ex[ex] = []
            seen.append(ex)
        by_ex[ex].append(s)

    for ex in seen:
        ex_sets = by_ex[ex]
        lines.append(f"<b>{_esc(ex)}</b>")
        for i, s in enumerate(ex_sets, 1):
            w = s.get("weight_kg")
            reps = s.get("reps")
            rt = s.get("reps_text")
            dur = s.get("duration_seconds")
            fail = s.get("is_failure", False)
            warmup = s.get("is_warmup", False)
            sg = s.get("superset_group")
            note = s.get("notes")

            tags = []
            if warmup:
                tags.append("разм")
            if sg:
                tags.append(sg)
            tag_str = f" [{', '.join(tags)}]" if tags else ""

            if dur:
                line = f"  {i}.{tag_str} {fmt_duration(dur)}"
            elif w is not None:
                reps_str = fmt_reps(reps, rt, fail)
                line = f"  {i}.{tag_str} {w:g} кг × {reps_str}"
            else:
                reps_str = fmt_reps(reps, rt, fail)
                line = f"  {i}.{tag_str} {reps_str} повт"
            if note:
                line += f" 💬 <i>{_esc(note)}</i>"
            lines.append(line)

    total = len(sets)
    lines.append(f"\n<i>Всего подходов: {total}</i>")
    return "\n".join(lines)


def format_set_confirmation(sets: list[dict]) -> str:
    """Compact confirmation after recording sets."""
    if not sets:
        return "✅ Записано"
    parts = []
    for s in sets:
        name = _esc(s.get("exercise_name", "?"))
        w = s.get("weight_kg")
        reps = s.get("reps")
        rt = s.get("reps_text")
        dur = s.get("duration_seconds")
        fail = s.get("is_failure", False)

        if dur:
            parts.append(f"{name}: {fmt_duration(dur)}")
        elif w is not None:
            reps_str = fmt_reps(reps, rt, fail)
            parts.append(f"{name}: {w:g} кг × {reps_str}")
        else:
            reps_str = fmt_reps(reps, rt, fail)
            parts.append(f"{name}: {reps_str} повт")
    return "✅ " + "\n✅ ".join(parts)


# ──────────────────────────── history formatters ─────────────────────────────

def format_workout_summary(workout: dict, sets: list[dict], zone=None) -> str:
    """One completed workout — compact summary."""
    d = workout.get("workout_date")
    focus = workout.get("focus_label") or "тренировка"
    started = workout.get("started_at")
    finished = workout.get("finished_at")

    time_range = ""
    if started and finished:
        s = _fmt_time(started, zone)
        f = _fmt_time(finished, zone)
        if s != "?" and f != "?":
            time_range = f" ({s}–{f})"

    lines = [f"🏋️ <b>{fmt_date(d)} — {_esc(focus)}</b>{time_range}"]
    w_notes = workout.get("notes")
    if w_notes:
        lines.append(f"💬 <i>{_esc(w_notes)}</i>")

    seen: list[str] = []
    by_ex: dict[str, list] = {}
    for s in sets:
        ex = s.get("exercise_name", "?")
        if ex not in by_ex:
            by_ex[ex] = []
            seen.append(ex)
        by_ex[ex].append(s)

    for ex in seen:
        ex_sets = by_ex[ex]
        # Compact: "Жим — 80×10, 80×8, 75×6"
        pieces = []
        notes_collected = []
        for s in ex_sets:
            w = s.get("weight_kg")
            reps = s.get("reps")
            rt = s.get("reps_text")
            dur = s.get("duration_seconds")
            fail = s.get("is_failure", False)
            if dur:
                pieces.append(fmt_duration(dur))
            elif w is not None:
                reps_str = fmt_reps(reps, rt, fail)
                pieces.append(f"{w:g}×{reps_str}")
            else:
                reps_str = fmt_reps(reps, rt, fail)
                pieces.append(reps_str)
            n = s.get("notes")
            if n:
                notes_collected.append(n)
        lines.append(f"  • {_esc(ex)}: {', '.join(pieces)}")
        for n in notes_collected:
            lines.append(f"    <i>💬 {_esc(n)}</i>")

    return "\n".join(lines)


def format_history_list(workouts_with_sets: list[tuple[dict, list[dict]]], zone=None) -> str:
    """Multiple workouts for history view."""
    if not workouts_with_sets:
        return "📭 Нет записанных тренировок."
    parts = [format_workout_summary(w, s, zone=zone) for w, s in workouts_with_sets]
    return "\n\n".join(parts)


def format_csv(workouts_with_sets: list[tuple[dict, list[dict]]]) -> str:
    """Export all sets as CSV string."""
    lines = ["date,focus,workout_notes,exercise,set_number,weight_kg,reps,reps_text,duration_seconds,is_warmup,is_failure,superset_group,set_notes"]
    for workout, sets in workouts_with_sets:
        d = workout.get("workout_date", "")
        focus = (workout.get("focus_label") or "").replace(",", ";")
        wn = (workout.get("notes") or "").replace(",", ";").replace("\n", " ")
        for s in sets:
            ex = (s.get("exercise_name") or "").replace(",", ";")
            sn = s.get("set_number", "")
            w = s.get("weight_kg", "")
            r = s.get("reps", "")
            rt = (s.get("reps_text") or "").replace(",", ";")
            dur = s.get("duration_seconds", "")
            iw = "1" if s.get("is_warmup") else "0"
            if_ = "1" if s.get("is_failure") else "0"
            sg = s.get("superset_group") or ""
            sn_notes = (s.get("notes") or "").replace(",", ";").replace("\n", " ")
            lines.append(f"{d},{focus},{wn},{ex},{sn},{w},{r},{rt},{dur},{iw},{if_},{sg},{sn_notes}")
    return "\n".join(lines)
