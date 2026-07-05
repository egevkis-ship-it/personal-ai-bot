"""
Pure-text set parser — no AI, no network.
Ported and cleaned from the old router_hardening.py.

parse_exercise_input(text, last_exercise) → list[SetResult]

Handles:
  - "Жим штанги 80×10"               classic W×R
  - "Hip trust 4x12 10 кг"           NxM W кг   (Pattern A)
  - "Сгибание ног 50кг 4х12"         Wкг NxM    (Pattern B)
  - "Жим 80 на 10 80 на 8 75 на 10"  multi-pair
  - "Подтягивания 10 8 7"            bodyweight reps-only
  - "Планка 60 секунд"               time-based
  - "Жим 80 на 10 до отказа"         last set to-failure
  - "тридцать пять килограмм на двенадцать"  spoken numbers
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SetResult:
    exercise_name: str
    weight_kg: float | None = None
    reps: int | None = None
    reps_text: str | None = None       # "AMRAP", "до отказа", "10-12"
    duration_seconds: int | None = None
    is_warmup: bool = False
    is_failure: bool = False
    superset_group: str | None = None
    notes: str | None = None


# ─────────────────── spoken-number normalisation ───────────────────────────

_SPOKEN = {
    "ноль": "0",
    "один": "1", "одна": "1",
    "два": "2", "две": "2",
    "три": "3", "четыре": "4",
    "пять": "5", "шесть": "6",
    "семь": "7", "восемь": "8",
    "девять": "9", "десять": "10",
    "одиннадцать": "11", "двенадцать": "12",
    "тринадцать": "13", "четырнадцать": "14",
    "пятнадцать": "15", "шестнадцать": "16",
    "семнадцать": "17", "восемнадцать": "18",
    "девятнадцать": "19",
    "двадцать": "20", "тридцать": "30",
    "сорок": "40", "пятьдесят": "50",
    "шестьдесят": "60", "семьдесят": "70",
    "восемьдесят": "80", "девяносто": "90",
    "сто": "100",
}
_ORDINALS_STRIP = {"первый", "второй", "третий", "четвертый", "пятый",
                   "шестой", "седьмой", "восьмой", "девятый", "десятый"}
# Only strip filler words here — "кг"/"килограмм" are preserved so that
# _parse_sets Pattern A/B can detect weight+kg markers.
# _parse_sets strips weight-units internally for its generic section.
_UNITS_STRIP = r"\b(раз|раза|повтор\w*|еще|следующий)\b"


def normalize_spoken(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    # Guard: only apply compound-number merging if spoken words are present,
    # otherwise "10 8 7" (reps sequence) gets wrongly merged into "18 7".
    _has_spoken = any(re.search(rf"\b{w}\b", t) for w in _SPOKEN)
    for word, val in _SPOKEN.items():
        t = re.sub(rf"\b{word}\b", val, t)
    for word in _ORDINALS_STRIP:
        t = re.sub(rf"\b{word}\b", "", t)
    if _has_spoken:
        # compound: "30 5" → "35"  (only when converted from spoken words)
        t = re.sub(
            r"\b(10|20|30|40|50|60|70|80|90)\s+([1-9])\b",
            lambda m: str(int(m.group(1)) + int(m.group(2))),
            t,
        )
    t = re.sub(_UNITS_STRIP, " ", t)
    return " ".join(t.split())


# ─────────────────────── split exercise / sets text ────────────────────────

_KG_PAT = r"(?:кг|килограмм\w*)"
_X = r"[×x✕х]"           # all cross variants


def _split_exercise_sets(raw: str) -> tuple[str, str]:
    """Return (exercise_name_raw, sets_text_raw).
    Splits at the first numeric token that looks like sets/weight.
    """
    raw = raw.strip()
    # Remove leading set-markers like "1)", "2." at the start
    raw = re.sub(r"^\d+[).]\s*", "", raw)

    # Find split point: first digit not inside a word
    m = re.search(r"\s(\d)", raw)
    if not m:
        return raw, ""
    return raw[:m.start()].strip(), raw[m.start():].strip()


# ─────────────────────── flexible sets parser ──────────────────────────────

def _parse_sets(sets_raw: str) -> list[tuple[float | None, int | None, str | None]]:
    """Return list of (weight, reps, reps_text) tuples.
    reps_text is set for AMRAP / до отказа / ranges.
    """
    raw = sets_raw.strip().lower().replace("ё", "е")
    raw = raw.replace("×", "x").replace("х", "x")
    _kg = r"(?:кг|килограмм\w*)"

    # ── Pattern A: NxM Wкг  e.g. "4x12 10 кг"
    ma = re.search(
        rf"(\d+)\s*x\s*(\d+)[\s,]+(\d+(?:[.,]\d+)?)\s*{_kg}",
        raw,
    )
    if ma:
        sets, reps, w = int(ma.group(1)), int(ma.group(2)), float(ma.group(3).replace(",", "."))
        return [(w, reps, None)] * sets

    # ── Pattern B: Wкг NxM  e.g. "50кг 4x12"
    mb = re.search(
        rf"(\d+(?:[.,]\d+)?)\s*{_kg}[\s,]*(\d+)\s*x\s*(\d+)",
        raw,
    )
    if mb:
        w, sets, reps = float(mb.group(1).replace(",", ".")), int(mb.group(2)), int(mb.group(3))
        return [(w, reps, None)] * sets

    # Strip units for generic parsing
    raw = re.sub(r"\b(кг|килограмм\w*)\b", " ", raw)
    raw = re.sub(r"\b(повторов|повтора|повтор|раза|раз)\b", " ", raw)
    raw = re.sub(r"\b(минут\w*|мин|секунд\w*|сек)\b", " ", raw)
    raw = " ".join(raw.split())

    # ── Count-by-reps: "70 4 по 14" → 4×(70,14)
    mc = re.search(r"(\d+(?:[.,]\d+)?)\s+(\d+)\s+по\s+(\d+)", raw)
    if mc:
        w, cnt, r = float(mc.group(1).replace(",", ".")), int(mc.group(2)), int(mc.group(3))
        return [(w, r, None)] * cnt

    # ── AMRAP / до отказа detection
    is_failure = bool(re.search(r"\b(до\s+отказа|amrap|макс\w*|failure)\b", raw))

    # ── NxM plain: "3x10", "4×12" or classic "80×10" (weight×reps)
    mn = re.search(r"(\d+(?:[.,]\d+)?)\s*x\s*(\d+)", raw)
    if mn:
        n1 = float(mn.group(1).replace(",", "."))
        n2 = int(mn.group(2))
        # check for explicit weight before the NxM token: "80 4×10"
        wm = re.search(r"(\d+(?:[.,]\d+)?)\s+\d+(?:[.,]\d+)?\s*x", raw)
        ft = "до отказа" if is_failure else None
        if wm:
            # weight is given separately; n1 = sets
            w = float(wm.group(1).replace(",", "."))
            return [(w, n2, ft)] * int(n1)
        elif n1 > 12:
            # Large first number → weight×reps (single set): "80×10" → 80kg × 10
            return [(n1, n2, ft)]
        else:
            # Small first number → sets×reps: "4×10" → 4 sets of 10
            return [(None, n2, ft)] * int(n1)

    # ── Explicit pairs: "80 на 10", "80x10", "80*10"
    pairs = []
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:на|по|x|\*)\s*(\d+)", raw):
        pairs.append((float(m.group(1).replace(",", ".")), int(m.group(2)), None))
    if pairs:
        # "100 на 5 5 5" extension
        if len(pairs) == 1:
            first = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:на|по|x|\*)\s*(\d+)(.*)", raw)
            if first:
                tail = first.group(3) or ""
                for extra_r in re.findall(r"\b(\d+)\b", tail):
                    pairs.append((pairs[0][0], int(extra_r), None))
        if is_failure:
            pairs[-1] = (pairs[-1][0], pairs[-1][1], "до отказа")
        return pairs

    # ── Reps-only: "10 8 7" (bodyweight)
    nums = re.findall(r"\d+(?:[.,]\d+)?", raw)
    if not nums:
        return []
    values = [float(x.replace(",", ".")) for x in nums]
    if all(v <= 50 for v in values):
        rt = "до отказа" if is_failure else None
        result = [(None, int(v), None) for v in values[:-1]]
        result.append((None, int(values[-1]), rt))
        return result

    # ── Weighted plain pairs: "80 10 80 8"
    if len(values) >= 4 and len(values) % 2 == 0 and any(v > 50 for v in values[::2]):
        return [(values[i], int(values[i + 1]), None) for i in range(0, len(values), 2)]

    # ── Single weight + reps: "80 10"
    if len(values) == 2 and values[0] > values[1]:
        return [(values[0], int(values[1]), "до отказа" if is_failure else None)]

    return []


# ─────────────────────────── main entry point ──────────────────────────────

_WARMUP = re.compile(r"\b(разминоч|разминк|разогрев|warmup|w/u)\b", re.I)
_SUPERSET = re.compile(r"\b(суперсет|двусет|супер[-\s]?сет|ss\d?|cc\d?)\b", re.I)
_CONTINUATION = re.compile(
    r"^(следующий|след|ещё|еще|ещe|\+|продолжу|продолжаем|тот\s+же|ещё\s+подход|след\.?\s+подход)\b",
    re.I,
)
_TIME_MIN = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:мин|минут\w*)\b", re.I)
_TIME_SEC = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:сек|секунд\w*)\b", re.I)


# REC-6: per-set qualifiers that must NOT split or scale the weight — they are just
# context. Stripped before number parsing (so «20 кг на руку 12» → 20 кг × 12) and
# kept as a note. «компенсация/противовес N» is the counterweight LOAD (W3-1): the
# word is stripped but its number stays as the weight.
_QUALIFIER_RE = re.compile(
    r"\b(на\s+руку|на\s+ногу|каждой(?:\s+(?:рукой|ногой))?|одной\s+(?:рукой|ногой)|"
    r"по\s+стороне|компенсаци\w*|противовес\w*)\b",
    re.IGNORECASE,
)


def parse_exercise_input(
    text: str,
    last_exercise: str | None = None,
) -> list[SetResult] | None:
    """
    Parse free-text exercise entry.
    Returns list of SetResult or None if text is not parseable as exercise input.
    """
    text = (text or "").strip()
    if not text:
        return None

    raw_original = text
    # Check flags on raw text BEFORE normalization —
    # normalize_spoken strips "следующий"/"еще" via _UNITS_STRIP.
    raw_lower = text.lower().replace("ё", "е")
    t = normalize_spoken(text)
    # REC-6: pull out qualifier phrases so they don't break number parsing; keep them
    # as a note attached to every set.
    _quals = [m.group(0).strip() for m in _QUALIFIER_RE.finditer(t)]
    qual_note = ", ".join(dict.fromkeys(_quals)) if _quals else None
    if _quals:
        t = re.sub(r"\s{2,}", " ", _QUALIFIER_RE.sub(" ", t)).strip()
        # removing a qualifier can leave «N кг M» dangling → rejoin as «N кг на M»
        # so the weight (before кг) parses instead of being read as a second rep.
        t = re.sub(r"(\d+(?:[.,]\d+)?\s*кг)\s+(\d)", r"\1 на \2", t, flags=re.IGNORECASE)
    t_lower = t.lower()

    is_continuation = bool(_CONTINUATION.match(raw_lower)) or raw_lower.startswith("следующий")
    is_warmup = bool(_WARMUP.search(raw_lower))
    is_superset = bool(_SUPERSET.search(raw_lower))

    # ── Time-based: "Планка 60 секунд", "Велосипед 10 минут" ──────────────
    m_min = _TIME_MIN.search(raw_original)
    m_sec = _TIME_SEC.search(raw_original)
    if m_min or m_sec:
        ex_name, _ = _split_exercise_sets(t)
        if not ex_name and last_exercise:
            ex_name = last_exercise
        if not ex_name:
            return None
        ex_name = _clean_name(ex_name)
        if m_min:
            secs = int(float(m_min.group(1).replace(",", ".")) * 60)
        else:
            secs = int(float(m_sec.group(1).replace(",", ".")))  # type: ignore[union-attr]
        return [SetResult(exercise_name=ex_name, duration_seconds=secs, is_warmup=is_warmup)]

    # ── Normal exercise + sets ─────────────────────────────────────────────
    if is_continuation and last_exercise:
        ex_name_raw = ""
        sets_raw = t
    else:
        ex_name_raw, sets_raw = _split_exercise_sets(t)

    # No sets data at all — not parseable
    if not sets_raw and not is_continuation:
        return None

    if is_continuation and last_exercise:
        ex_name = last_exercise
    elif ex_name_raw and not ex_name_raw[0].isdigit():
        # Normal: has an alphabetic name prefix
        ex_name = _clean_name(ex_name_raw)
    else:
        # No name prefix or starts with digit (spoken-number/weight-first input)
        # → use last_exercise AND treat the full normalized text as sets data
        ex_name = last_exercise or ""
        sets_raw = t  # e.g. "35 килограмм на 12" → pass whole thing to _parse_sets

    if not ex_name:
        return None

    pairs = _parse_sets(sets_raw)
    if not pairs:
        return None

    sg = "A" if is_superset else None
    results = []
    for w, r, rt in pairs:
        is_failure = rt == "до отказа"
        results.append(SetResult(
            exercise_name=ex_name,
            weight_kg=w,
            reps=r,
            reps_text=rt,
            is_warmup=is_warmup,
            is_failure=is_failure,
            superset_group=sg,
            notes=qual_note,   # REC-6: qualifier kept as a note
        ))
    return results if results else None


def _clean_name(name: str) -> str:
    name = re.sub(r"\b(разминоч\w*|разминк\w*|разогрев\w*|warmup|w/u)\b", "", name, flags=re.I)
    name = " ".join(name.split())
    if name:
        name = name[0].upper() + name[1:]
    return name


def looks_like_exercise_input(text: str) -> bool:
    """Quick gate — is this text likely an exercise log entry?"""
    t = normalize_spoken(text)
    tl = t.lower()

    # Time-based
    if re.search(r"\b\d+\s*(мин|минут\w*|сек|секунд\w*)\b", text, re.I):
        return True
    # continuation
    if _CONTINUATION.match(tl):
        return True
    # Has alpha AND digits with set-like patterns
    if re.search(r"[а-яa-z]{3,}.*\d+", tl, re.I):
        if re.search(r"[×x]\s*\d+|\d+\s*(на|по|x)\s*\d+|\d+\s+\d+", tl, re.I):
            return True
        # bodyweight: "Подтягивания 10 8 7"
        if re.search(r"[а-яa-z]{4,}\s+\d+(?:\s+\d+)+", tl, re.I):
            return True
    return False
