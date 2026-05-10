from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.modules.fitness.program_importer import (
    parse_training_program_text,
    training_program_to_dict,
    format_program_import_preview,
)


WEEKDAY_MAP = {
    "пн": 0,
    "понедельник": 0,
    "понедельникам": 0,
    "вт": 1,
    "вторник": 1,
    "вторникам": 1,
    "ср": 2,
    "среда": 2,
    "среду": 2,
    "средам": 2,
    "чт": 3,
    "четверг": 3,
    "четвергам": 3,
    "пт": 4,
    "пятница": 4,
    "пятницу": 4,
    "пятницам": 4,
    "сб": 5,
    "суббота": 5,
    "субботу": 5,
    "субботам": 5,
    "вс": 6,
    "воскресенье": 6,
    "воскресеньям": 6,
}


def _clean(text: str | None) -> str:
    return (text or "").strip().lower().replace("ё", "е")


def _today() -> date:
    return date.today()


def _next_date_for_weekday(weekday: int, start: date | None = None, include_today: bool = False) -> date:
    base = start or _today()
    delta = weekday - base.weekday()
    if delta < 0 or (delta == 0 and not include_today):
        delta += 7
    return base + timedelta(days=delta)


def _parse_weeks_count(text: str | None, default: int = 1) -> int:
    t = _clean(text)

    m = re.search(r"\b(\d{1,2})\s*(?:недел|недели|неделю)\b", t)
    if m:
        return max(1, min(int(m.group(1)), 52))

    if "месяц" in t:
        return 4

    if "3 месяца" in t or "три месяца" in t:
        return 12

    if "следующие недели" in t or "следующие недел" in t:
        return 4

    return default


def _parse_weekday_layout(text: str | None, day_count: int) -> list[int] | None:
    t = _clean(text)

    # Common layouts
    compact = re.sub(r"[,/]+", " ", t)
    compact = re.sub(r"\s+", " ", compact)

    if all(x in compact for x in ["пн", "ср", "пт", "сб"]):
        return [0, 2, 4, 5]

    if all(x in compact for x in ["пн", "ср", "пт"]):
        return [0, 2, 4]

    if all(x in compact for x in ["вт", "чт", "сб"]):
        return [1, 3, 5]

    weekdays = []
    # Longer names first to reduce false positives.
    for key, value in sorted(WEEKDAY_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"(?<![а-яa-z]){re.escape(key)}(?![а-яa-z])", compact):
            if value not in weekdays:
                weekdays.append(value)

    if weekdays:
        return sorted(weekdays)

    if "через день" in t:
        # Not week-day based. Caller handles this via special mode.
        return []

    # Default for 4-day program.
    if day_count >= 4:
        return [0, 2, 4, 5]

    if day_count == 3:
        return [0, 2, 4]

    if day_count == 2:
        return [0, 3]

    return [0]


def _build_dates_by_weekdays(weekdays: list[int], weeks: int, start: date | None = None) -> list[str]:
    base = start or _today()
    first_monday = base - timedelta(days=base.weekday())

    dates = []
    for week_index in range(weeks):
        week_start = first_monday + timedelta(days=7 * week_index)
        for weekday in weekdays:
            d = week_start + timedelta(days=weekday)
            if d < base:
                continue
            dates.append(d.isoformat())

    return dates


def _build_dates_every_other_day(count: int, start: date | None = None) -> list[str]:
    base = start or _today()
    return [(base + timedelta(days=i * 2)).isoformat() for i in range(count)]


def _format_import_result(result: dict) -> str:
    created = result.get("created") or []
    skipped = result.get("skipped") or []

    lines = ["Импорт программы завершён.", ""]

    lines.append(f"Создано тренировок: {len(created)}")
    for item in created:
        lines.append(f"- {item.get('target_date')}: {item.get('title')}")

    if skipped:
        lines.append("")
        lines.append(f"Пропущено: {len(skipped)}")
        for item in skipped:
            date_s = item.get("target_date") or "дата не указана"
            reason = item.get("reason") or item.get("message") or "не создано"
            existing = item.get("existing_title")
            if existing:
                lines.append(f"- {date_s}: уже есть активная тренировка — {existing}")
            else:
                lines.append(f"- {date_s}: {reason}")

    return "\n".join(lines)


def looks_like_training_program_text(text: str | None) -> bool:
    t = _clean(text)
    if not t:
        return False

    has_days = bool(re.search(r"\bдень\s+\d+\b", t))
    has_program_word = "программа" in t and "трениров" in t
    has_many_exercises = len(re.findall(r"\n\s*\d+[\.\)]\s+", text or "")) >= 4

    return has_days or has_program_word or has_many_exercises


async def preview_training_program_import(
    telegram_user_id: str | None,
    program_text: str,
    source_type: str = "text",
    title: str | None = None,
) -> str:
    from app.db import create_fitness_pending_decision

    program = parse_training_program_text(
        program_text,
        title=title or "Импортированная программа тренировок",
        source_type=source_type,
    )

    program_dict = training_program_to_dict(program)

    await create_fitness_pending_decision(
        telegram_user_id=telegram_user_id,
        decision_type="pending_training_program_import",
        context={
            "program": program_dict,
            "source_type": source_type,
            "source_text": program_text,
        },
        source_text=program_text[:4000],
    )

    preview = format_program_import_preview(program)

    return (
        preview
        + "\n\nКак разложить по календарю?\n"
        + "- `пн ср пт сб на 1 неделю`\n"
        + "- `пн ср пт сб на 4 недели`\n"
        + "- `через день`\n"
        + "- `отмена`"
    )


async def handle_training_program_import_pending(
    telegram_user_id: str | None,
    text: str,
) -> str | None:
    from app.db import (
        get_latest_fitness_pending_decision,
        resolve_fitness_pending_decision,
        import_training_program_to_calendar,
    )

    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if not pending or pending.get("decision_type") != "pending_training_program_import":
        return None

    t = _clean(text)

    if t in {"отмена", "отмени", "не надо", "стоп"}:
        await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return "Ок, импорт программы отменён."

    context = pending.get("context_json") or {}
    program = context.get("program")
    source_text = context.get("source_text")

    if not program:
        await resolve_fitness_pending_decision(pending["id"], status="failed")
        return "Не нашёл сохранённую программу для импорта. Отправь программу ещё раз."

    days = program.get("days") or []
    if not days:
        await resolve_fitness_pending_decision(pending["id"], status="failed")
        return "В программе нет тренировочных дней."

    weeks = _parse_weeks_count(text, default=1)
    layout = _parse_weekday_layout(text, day_count=len(days))

    if layout == [] and "через день" in t:
        target_dates = _build_dates_every_other_day(count=len(days) * weeks)
    elif layout:
        target_dates = _build_dates_by_weekdays(layout, weeks=weeks)
    else:
        return (
            "Не понял расклад. Напиши, например:\n"
            "- пн ср пт сб на 1 неделю\n"
            "- пн ср пт сб на 4 недели\n"
            "- через день\n"
            "- отмена"
        )

    # Only create as many dates as the repeated program needs.
    # If layout has more days than the program, extra dates are OK: program days cycle.
    expected_count = len(days) * weeks
    if len(target_dates) > expected_count:
        target_dates = target_dates[:expected_count]

    result = await import_training_program_to_calendar(
        telegram_user_id=telegram_user_id,
        program=program,
        target_dates=target_dates,
        title_prefix=None,
        skip_existing=True,
        source_text=source_text,
    )

    await resolve_fitness_pending_decision(pending["id"], status="resolved")

    return _format_import_result(result)
