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

    Important:
    - Action words like "перенеси", "дай", "покажи", "замени" are NOT enough.
    - We only route to fitness when there is a fitness-specific anchor.
    """

    if not text:
        return False

    t = text.lower()

    muscle_anchors = [
        "грудь", "спина", "плечи", "ноги", "руки",
        "бицепс", "трицепс", "пресс", "дельты", "ягодицы", "икры",
    ]

    workout_anchors = [
        "тренировка", "треню", "потрен", "зал",
        "упражнение", "подход", "подходы", "повтор", "повторы",
        "rpe", "рабочий вес", "разминка",
    ]

    exercise_anchors = [
        "жим", "присед", "тяга", "подтяг", "разводка", "махи",
        "брусья", "становая", "жим ногами", "тяга блока", "гантел",
        "штанг",
    ]

    measurement_anchors = [
        "вес утром", "мой вес", "вес тела", "талия", "замеры",
        "обхват", "грудь ", "рука ", "бедро ", "шея ",
    ]

    action_words = [
        "перенеси", "перенести", "поменяй", "замени", "заменить",
        "пропусти", "пропустил", "пропускаем",
        "дай", "покажи", "что сегодня", "что дальше",
    ]

    has_fitness_anchor = any(w in t for w in muscle_anchors + workout_anchors + exercise_anchors + measurement_anchors)
    has_action = any(w in t for w in action_words)

    # Plan/change/query cases:
    # "перенеси спину", "дай грудь", "покажи тренировку", "замени ноги"
    if has_fitness_anchor and has_action:
        return True

    # Explicit plan:
    # "план на неделю: грудь, спина..."
    if "план" in t and has_fitness_anchor:
        return True

    # Workout fact:
    # "сделал грудь", "выполнил жим", "сегодня была тренировка"
    if any(w in t for w in ["сделал", "выполнил", "была тренировка", "сегодня тренировка"]) and has_fitness_anchor:
        return True

    # Typical workout numbers:
    # "жим 80 на 10", "разводка 16 по 12"
    if any(w in t for w in exercise_anchors) and any(w in t for w in [" на ", " по ", "×", "x"]):
        return True

    # Body measurements:
    if any(w in t for w in measurement_anchors):
        return True

    return False
