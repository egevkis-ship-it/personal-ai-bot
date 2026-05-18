"""
Классификатор упражнений по группам мышц.

Используется для:
- volume_by_group
- lagging_group analysis
- plateau detection
- coach report
"""
from __future__ import annotations
import re

# Паттерны: regex → main group
# Порядок важен: более специфичные паттерны вверху
_PATTERNS: list[tuple[str, str]] = [
    # Плечи (специфичные паттерны первыми)
    (r"reverse.*pec|задн.*дельт|махи.*заднюю", "плечи"),
    (r"жим.*плеч|жим.*сидя|жим.*армейск|жим.*стоя|махи.*сторон|махи.*перед|боковая дельта|плечо\b", "плечи"),
    # Грудь
    (r"жим.*лёж|жим.*лежа|bench|pec.?deck|сведен|разводк.*груд|жим.*угл|жим.*тренаж", "грудь"),
    # Спина
    (r"тяг.*блок|тяг.*штанг|тяг.*гантел|подтяг|становая|гипер|пуловер|шраг|гравитрон|вертикальн.*тяг|горизонтальн.*тяг", "спина"),
    # Бицепс
    (r"бицепс|сгибан.*рук|скотт|молотк|hammer|подъём на бицепс", "бицепс"),
    # Трицепс
    (r"трицепс|разгибан.*рук|жим.*узк|француз|канат|разгибан.*трицеп|brus|брус.*узк", "трицепс"),
    # Ноги: квадры
    (r"присед|жим.*ног|разгибан.*ног|выпад|sissy|hack squat|болгар", "квадрицепс"),
    # Ноги: бицепс бедра
    (r"сгибан.*ног|румынск|румынка|good.?morning|hip.?thrust|ягодиц|glute|отведен.*бёдер|отведен.*бедер|hip.?abduction", "ягодицы"),
    # Икры
    (r"икр|calf|подъём на носк", "икры"),
    # Пресс
    (r"пресс|кранч|скручиван|планка|v.?склад|hanging|ноги.*висе|abs", "пресс"),
    # Кардио
    (r"дорожк|бег|велик|велосипед|эллипс|cycle|run|stairmaster|cardio|кардио|плаван", "кардио"),
    # Брусья — отдельная категория (грудь+трицепс)
    (r"брусь", "трицепс"),
]


def classify_exercise(name: str | None) -> str:
    """Return primary muscle group for an exercise name, or 'другое'."""
    if not name:
        return "другое"
    t = name.lower().replace("ё", "е")
    for pattern, group in _PATTERNS:
        if re.search(pattern, t):
            return group
    return "другое"


# Общая группа для агрегаций
GROUP_MAJOR: dict[str, str] = {
    "грудь": "верх_тяни_толкай",
    "спина": "верх_тяни_толкай",
    "плечи": "верх_тяни_толкай",
    "бицепс": "руки",
    "трицепс": "руки",
    "квадрицепс": "ноги",
    "ягодицы": "ноги",
    "икры": "ноги",
    "пресс": "кор",
    "кардио": "кардио",
    "другое": "другое",
}


def major_group(group: str) -> str:
    return GROUP_MAJOR.get(group, "другое")


def aggregate_by_group(sets: list[dict]) -> dict[str, dict]:
    """Aggregate sets by muscle group → {group: {sets: N, tonnage: X, exercises: set, max_w: Y}}"""
    out: dict[str, dict] = {}
    for s in sets:
        g = classify_exercise(s.get("exercise_name"))
        if g not in out:
            out[g] = {"sets": 0, "tonnage": 0.0, "exercises": set(), "max_w": 0.0, "reps_total": 0}
        out[g]["sets"] += 1
        if s.get("exercise_name"):
            out[g]["exercises"].add(s["exercise_name"])
        try:
            w = float(s.get("weight_kg") or 0)
            r = int(s.get("reps") or 0)
            out[g]["tonnage"] += w * r
            out[g]["reps_total"] += r
            if w > out[g]["max_w"]:
                out[g]["max_w"] = w
        except Exception:
            pass
    # Convert exercises set to count
    for g in out:
        out[g]["exercises_count"] = len(out[g]["exercises"])
        out[g]["exercises"] = sorted(out[g]["exercises"])
    return out


def estimate_1rm(weight: float, reps: int) -> float:
    """Epley formula: 1RM = weight * (1 + reps/30). Capped at reps≤12."""
    try:
        w = float(weight)
        r = int(reps)
        if r <= 0:
            return 0.0
        if r == 1:
            return w
        r = min(r, 12)
        return round(w * (1 + r / 30), 1)
    except Exception:
        return 0.0
