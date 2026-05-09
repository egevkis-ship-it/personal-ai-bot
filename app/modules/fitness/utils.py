from __future__ import annotations
from datetime import date, datetime, timedelta


WEEKDAY_RU_TO_NUM = {
    "понедельник": 0,
    "пн": 0,
    "вторник": 1,
    "вт": 1,
    "среда": 2,
    "ср": 2,
    "четверг": 3,
    "чт": 3,
    "пятница": 4,
    "пт": 4,
    "суббота": 5,
    "сб": 5,
    "воскресенье": 6,
    "вс": 6,
}

WEEKDAY_NUM_TO_RU = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}

FOCUS_MAP = {
    "грудь": "chest",
    "грудные": "chest",
    "спина": "back",
    "плечи": "shoulders",
    "дельты": "shoulders",
    "ноги": "legs",
    "квадрицепс": "legs",
    "бицепс": "arms",
    "трицепс": "arms",
    "руки": "arms",
    "пресс": "abs",
    "кардио": "cardio",
    "фулбади": "full_body",
    "full body": "full_body",
    "фуллбади": "full_body",
}


def today_iso() -> str:
    return date.today().isoformat()


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def week_bounds(target: date | None = None) -> tuple[str, str]:
    target = target or date.today()
    start = target - timedelta(days=target.weekday())
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def next_week_bounds() -> tuple[str, str]:
    today = date.today()
    start = today - timedelta(days=today.weekday()) + timedelta(days=7)
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def normalize_focus(label: str | None) -> tuple[str | None, str | None]:
    if not label:
        return None, None

    clean = label.strip().lower()
    focus = FOCUS_MAP.get(clean)

    if focus:
        return focus, label.strip()

    return clean.replace(" ", "_"), label.strip()


def format_number(value) -> str:
    if value is None:
        return ""
    try:
        value = float(value)
        if value.is_integer():
            return str(int(value))
        return str(value)
    except Exception:
        return str(value)


def short_weekday_from_date(date_str: str | None) -> str | None:
    d = parse_iso_date(date_str)
    if not d:
        return None
    return WEEKDAY_NUM_TO_RU[d.weekday()]


def is_likely_fitness_text(text: str | None) -> bool:
    """
    Conservative pre-router for obvious fitness messages.

    Action words like "перенеси", "дай", "покажи", "замени" are NOT enough.
    We only route to fitness when there is a fitness-specific anchor.
    Russian words are matched by stems to support cases: спина/спину, тренировка/тренировку.
    """

    if not text:
        return False

    t = text.lower()

    fitness_anchors = [
        # muscle groups / stems
        "груд", "спин", "плеч", "ног", "рук",
        "бицепс", "трицепс", "пресс", "дельт", "ягодиц", "икр",

        # workout context
        "трениров", "треня", "треню", "трени", "треньк", "тренечк",
        "потрен", "зал", "воркаут", "workout",
        "план тренировок", "тренировочный план", "программа тренировок", "прога",
        "упражнен", "подход", "повтор", "rpe",
        "рабочий вес", "размин",

        # exercises
        "жим", "присед", "тяга", "подтяг", "разводк", "мах",
        "брусь", "станов", "гантел", "штанг",

        # measurements
        "вес утром", "мой вес", "вес тела", "талия", "замер", "обхват",
    ]

    action_words = [
        "перенеси", "перенести",
        "поменяй", "замени", "заменить",
        "убери", "убрать", "удали", "удалить", "исключи",
        "добавь", "добавить",
        "пропусти", "пропустил", "пропускаем",
        "дай", "покажи",
        "что сегодня", "что дальше",
        "сегодня",
        "сегодняшнюю",
        "на сегодня",
        "следующий",
        "третий",
        "второй подход",
        "первый подход",
        "начинаем",
        "запиши тренировку",
        "записываем",
        "что у меня",
        "какой",
        "какая",
    ]

    has_fitness_anchor = any(w in t for w in fitness_anchors)
    has_action = any(w in t for w in action_words)

    if has_fitness_anchor and has_action:
        return True

    if "план" in t and has_fitness_anchor:
        return True

    if any(w in t for w in ["сделал", "выполнил", "была тренировка", "сегодня тренировка"]) and has_fitness_anchor:
        return True

    if any(w in t for w in ["жим", "присед", "тяга", "разводк", "мах", "подтяг"]) and any(w in t for w in [" на ", " по ", "×", "x"]):
        return True

    if any(w in t for w in ["вес утром", "мой вес", "вес тела", "талия", "замер", "обхват"]):
        return True

    return False



def month_bounds(today=None):
    from datetime import date, timedelta

    if today is None:
        today = date.today()

    start = today.replace(day=1)

    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)

    end = next_month - timedelta(days=1)
    return start.isoformat(), end.isoformat()



def next_month_bounds(today=None):
    from datetime import date, timedelta

    if today is None:
        today = date.today()

    if today.month == 12:
        start = today.replace(year=today.year + 1, month=1, day=1)
    else:
        start = today.replace(month=today.month + 1, day=1)

    if start.month == 12:
        after_next = start.replace(year=start.year + 1, month=1, day=1)
    else:
        after_next = start.replace(month=start.month + 1, day=1)

    end = after_next - timedelta(days=1)
    return start.isoformat(), end.isoformat()
