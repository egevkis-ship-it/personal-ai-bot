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

    if "через день" in t:
        # Not week-day based. Caller handles this via special mode.
        return []

    compact = re.sub(r"[,/;]+", " ", t)
    compact = re.sub(r"\s+", " ", compact).strip()

    # Explicit token parsing for short forms: пн вт чт пт.
    token_map = {
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

    weekdays = []
    for token in compact.split():
        token = token.strip().lower()
        if token in token_map and token_map[token] not in weekdays:
            weekdays.append(token_map[token])

    if weekdays:
        return weekdays

    # Fallback by substring for phrases like “по понедельникам, средам и пятницам”.
    for key, value in sorted(WEEKDAY_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"(?<![а-яa-z]){re.escape(key)}(?![а-яa-z])", compact):
            if value not in weekdays:
                weekdays.append(value)

    if weekdays:
        return weekdays

    # Default only if user did not give an explicit but unrecognized layout.
    if day_count >= 4:
        return [0, 2, 4, 5]
    if day_count == 3:
        return [0, 2, 4]
    if day_count == 2:
        return [0, 3]
    return [0]


def _build_dates_by_weekdays(weekdays: list[int], weeks: int, start: date | None = None) -> list[str]:
    """
    Build nearest future dates for a weekday layout.

    Important:
    If today is Sunday and user says “пн вт чт пт”,
    target dates must be next Mon/Tue/Thu/Fri, not past days of current week.
    """
    base = start or _today()

    dates = []
    for week_index in range(weeks):
        for weekday in weekdays:
            first = _next_date_for_weekday(weekday, start=base, include_today=True)
            d = first + timedelta(days=7 * week_index)
            dates.append(d.isoformat())

    # Deduplicate while keeping chronological order.
    dates = sorted(set(dates))
    return dates


def _build_dates_every_other_day(count: int, start: date | None = None) -> list[str]:
    base = start or _today()
    return [(base + timedelta(days=i * 2)).isoformat() for i in range(count)]


def _format_import_result(result: dict, target_dates: list[str] | None = None) -> str:
    created = result.get("created") or []
    skipped = result.get("skipped") or []
    replaced = result.get("replaced") or []

    lines = ["Импорт программы завершён.", ""]

    if target_dates:
        lines.append("Расклад:")
        for index, date_s in enumerate(target_dates, start=1):
            lines.append(f"- День {index}: {date_s}")
        lines.append("")

    if replaced:
        lines.append(f"Заменено: {len(replaced)}")
        for item in replaced:
            date_s = item.get("target_date") or "дата не указана"
            title = item.get("title") or "Плановая тренировка"
            lines.append(f"- {date_s}: {title}")
        lines.append("")

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

    if not created and not skipped:
        lines.append("")
        lines.append("Ни одной тренировки не создано и не пропущено. Это ошибка построения дат импорта.")

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




def _parse_import_collision_policy(text: str | None) -> str:
    t = _clean(text)

    replace_markers = [
        "заменить старые",
        "замени старые",
        "заменять старые",
        "заменить существующие",
        "замени существующие",
        "заменять существующие",
        "заменить занятые",
        "замени занятые",
        "заменять занятые",
        "перезаписать",
        "перезапиши",
        "поверх старых",
        "вместо старых",
    ]

    skip_markers = [
        "пропускать занятые",
        "пропусти занятые",
        "не трогай старые",
        "не заменяй старые",
        "оставь старые",
        "без замены",
    ]

    if any(x in t for x in replace_markers):
        return "replace_existing"

    if any(x in t for x in skip_markers):
        return "skip_existing"

    # Safe default.
    return "skip_existing"


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

    # If user sends a new full program while an old import is waiting for schedule,
    # do not treat the new program as a schedule answer.
    # Replace the pending import and show a fresh preview.
    if looks_like_training_program_text(text):
        await resolve_fitness_pending_decision(pending["id"], status="cancelled")
        return await preview_training_program_import(
            telegram_user_id=telegram_user_id,
            program_text=text,
            source_type="text",
            title="Импортированная программа тренировок",
        )

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
    collision_policy = _parse_import_collision_policy(text)

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

    if not target_dates:
        return (
            "Не смог построить даты для импорта. "
            "Укажи расклад, например: `пн вт чт пт`, `пн ср пт сб` или `через день`."
        )

    try:
        result = await import_training_program_to_calendar(
            telegram_user_id=telegram_user_id,
            program=program,
            target_dates=target_dates,
            title_prefix=None,
            skip_existing=(collision_policy != "replace_existing"),
            source_text=source_text,
        )
    except Exception as exc:
        return (
            "Не смог импортировать программу в календарь. "
            f"Ошибка: {type(exc).__name__}: {exc}"
        )

    await resolve_fitness_pending_decision(pending["id"], status="resolved")

    return _format_import_result(result, target_dates=target_dates)
