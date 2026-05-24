"""
Parse a free-text or voice-transcribed body-measurement message.

Examples it handles:
  "вес 102.7 голень 44.5 бедро 68 бёдра 104 живот 95 талия 92.5 грудь 106 рука 41 шея 40"
  "24.05.2026: вес 102.7, голень 44.5, бедро 68"
  "сегодня 102.3 кг, талия 92"
  Multiline, with colons, commas, or pure whitespace.

Output: dict with optional keys:
  date (ISO str), values (dict of metric→float), notes (str|None), missing (list[str])
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta

import anthropic

from app.config import settings

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


# canonical field → list of trigger words (lowercased, no ё)
_FIELD_WORDS = {
    "weight_kg":  ["вес", "weight", "масса", "массу"],
    "calf_cm":    ["голень", "голеней", "икра", "икры"],
    "thigh_cm":   ["бедро", "бедра"],            # singular: one leg upper
    "hips_cm":    ["бедра", "бeдра", "ягодицы", "таз", "hips"],  # plural/hips
    "belly_cm":   ["живот", "пузо"],
    "waist_cm":   ["талия", "талию", "waist"],
    "chest_cm":   ["грудь", "груди", "chest"],
    "arm_cm":     ["рука", "руки", "бицепс", "бицепса", "arm"],
    "neck_cm":    ["шея", "шеи", "neck"],
}

# Order matters: "бёдра" (plural=hips) and "бедро" (singular=thigh) both reduce
# to "бедра" after ё→е normalization. We disambiguate by surface form:
# - matched substring in original text contains "ё" → hips/thigh decision below
# - "бёдра" / "ягодицы" / "таз" → hips
# - "бедро" → thigh
# The map above is heuristic; the actual matcher below decides per-occurrence.

ALL_FIELDS = list(_FIELD_WORDS.keys())

_HUMAN_RU = {
    "weight_kg":  "вес",
    "calf_cm":    "голень",
    "thigh_cm":   "бедро",
    "hips_cm":    "бёдра",
    "belly_cm":   "живот",
    "waist_cm":   "талия",
    "chest_cm":   "грудь",
    "arm_cm":     "рука",
    "neck_cm":    "шея",
}


def field_label_ru(field: str) -> str:
    return _HUMAN_RU.get(field, field)


# Match a number followed/preceded by a unit
_NUMBER = r"(\d+(?:[.,]\d+)?)"

# Only full-date patterns: must include a year (4 digits) or 2-digit year via DD.MM.YY.
# This avoids stealing measurements like "102.7" or "44.5".
_DATE_PATTERNS = [
    (r"\b(\d{4}-\d{2}-\d{2})\b", "%Y-%m-%d"),            # 2026-05-24
    (r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b", "%d.%m.%Y"),      # 24.05.2026
    (r"\b(\d{1,2}/\d{1,2}/\d{4})\b", "%d/%m/%Y"),
    (r"\b(\d{1,2}-\d{1,2}-\d{4})\b", "%d-%m-%Y"),
    (r"\b(\d{1,2}\.\d{1,2}\.\d{2})\b", "%d.%m.%y"),      # 24.05.26
]


def _parse_date(text: str) -> tuple[date | None, tuple[int, int] | None]:
    """Returns (date, (start,end) of date token in original text)."""
    s = text.lower()
    today = date.today()
    if m := re.search(r"\bсегодня\b", s):
        return today, m.span()
    if m := re.search(r"\bвчера\b", s):
        return today - timedelta(days=1), m.span()
    if m := re.search(r"\bпозавчера\b", s):
        return today - timedelta(days=2), m.span()
    for pat, fmt in _DATE_PATTERNS:
        m = re.search(pat, s)
        if not m:
            continue
        token = m.group(1)
        try:
            d = datetime.strptime(token, fmt).date()
            return d, m.span()
        except ValueError:
            continue
    return None, None


def _normalize_text(s: str) -> str:
    return s.lower().replace("ё", "е")


def parse_measurement_text(raw: str) -> dict:
    """Pure-text first pass.

    Returns:
      {
        "date": date | None,
        "values": {field: float, ...},
        "raw": original text,
      }
    """
    text = raw or ""
    out_values: dict[str, float] = {}

    # Date extraction first — only blank out the EXACT date token so we don't
    # eat measurement numbers like "102.7" / "44.5".
    d, date_span = _parse_date(text)
    if date_span:
        text_for_metrics = (
            text[: date_span[0]] + " " * (date_span[1] - date_span[0]) + text[date_span[1]:]
        )
    else:
        text_for_metrics = text

    # Walk every occurrence of every alias; pick the closest number.
    norm = _normalize_text(text_for_metrics)

    # 1) "бёдра" with explicit ё in ORIGINAL text → hips
    if re.search(r"\bб[её]дра\b", text):
        if "ё" in text.lower():
            ...  # both bedra and bёdra map to the same lemma after normalization

    # We'll iterate the normalized text and tag each known unit-word with its
    # preferred field; numbers within ~16 chars after it bind to that field.
    spans: list[tuple[int, int, str]] = []  # (start, end, field)

    def _add_spans(field: str, words: list[str]):
        for w in words:
            for m in re.finditer(rf"\b{re.escape(w)}\b", norm):
                spans.append((m.start(), m.end(), field))

    # First add thigh (бедро singular), then hips (бёдра plural — matches "бедра").
    for field, words in _FIELD_WORDS.items():
        _add_spans(field, words)

    # Resolve ambiguity: in normalized text "бедра" matches both thigh and hips.
    # Disambiguate using ORIGINAL text near the same position:
    #   contains ё → hips, else thigh.
    cleaned_spans: list[tuple[int, int, str]] = []
    seen_positions: set[tuple[int, int]] = set()
    for start, end, field in spans:
        key = (start, end)
        if key in seen_positions:
            continue
        if field in ("thigh_cm", "hips_cm"):
            # extract the matched word from original text at corresponding position
            # offsets between orig and norm differ only by ё→е (single char) so they align
            orig_word = text[start:end].lower()
            if "ё" in orig_word:
                field = "hips_cm"
            else:
                field = "thigh_cm"
        cleaned_spans.append((start, end, field))
        seen_positions.add(key)

    # Sort by start
    cleaned_spans.sort()

    # For each span, find the FIRST numeric token after it (and before the next span)
    for i, (s, e, field) in enumerate(cleaned_spans):
        next_start = cleaned_spans[i + 1][0] if i + 1 < len(cleaned_spans) else len(norm)
        window = norm[e:next_start]
        m = re.search(_NUMBER, window)
        if m:
            try:
                val = float(m.group(1).replace(",", "."))
            except ValueError:
                continue
            # Don't overwrite if already set (first occurrence wins)
            if field not in out_values:
                out_values[field] = val

    # Compose notes: anything in original text that looks like prose
    return {
        "date": d.isoformat() if d else None,
        "values": out_values,
        "raw": raw,
    }


# ─────────────────────────── AI fallback ─────────────────────────────────────

_AI_SYSTEM = """\
Ты парсер замеров тела из тренировочного дневника. На вход — текст или \
голосовое распознавание (возможны ошибки Whisper). На выход — строгий JSON:

{"date":"2026-05-24","values":{"weight_kg":102.7,"calf_cm":44.5,"thigh_cm":68,"hips_cm":104,"belly_cm":95,"waist_cm":92.5,"chest_cm":106,"arm_cm":41,"neck_cm":40},"notes":null}

Поля values:
  weight_kg — вес (кг)
  calf_cm   — голень/икра (см)
  thigh_cm  — бедро ОДНА нога вверху (см) — слово "бедро" единственное число
  hips_cm   — бёдра/обхват таза-ягодиц (см) — слово "бёдра" множественное
  belly_cm  — живот
  waist_cm  — талия
  chest_cm  — грудь
  arm_cm    — рука/бицепс
  neck_cm   — шея

Правила:
- ISO дата если есть, иначе null (вызывающий код подставит сегодня)
- Пропущенные параметры — не указывай в values (НЕ ставь null)
- "сегодня"/"вчера" → конкретная дата ISO
- Whisper-ошибки исправляй: "бёдро"/"бёдра" различай по числу
- Никаких пояснений вне JSON
"""


async def ai_parse_measurement(raw: str) -> dict | None:
    """AI fallback. Returns same shape as parse_measurement_text or None."""
    try:
        resp = await _anthropic.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content": raw}],
        )
        text_out = resp.content[0].text.strip()
        if text_out.startswith("```"):
            text_out = text_out.split("\n", 1)[1].rsplit("```", 1)[0]
        # Find JSON
        i = text_out.find("{")
        j = text_out.rfind("}")
        if i < 0 or j < 0:
            return None
        data = json.loads(text_out[i:j + 1])
        return {
            "date": data.get("date"),
            "values": data.get("values") or {},
            "raw": raw,
            "notes": data.get("notes"),
        }
    except Exception as exc:
        log.error("ai_parse_measurement error: %s", exc)
        return None


async def parse_measurement(raw: str) -> dict:
    """Hybrid pipeline: pure-text first, AI fallback for sparse results."""
    pure = parse_measurement_text(raw)
    # Threshold: if at least 3 metrics parsed → trust pure parser.
    if len(pure["values"]) >= 3:
        return pure
    # Otherwise try AI to recover missed fields.
    ai = await ai_parse_measurement(raw)
    if not ai:
        return pure
    # Merge: AI fills only what pure missed.
    merged = dict(ai.get("values") or {})
    for k, v in pure["values"].items():
        merged[k] = v
    return {
        "date": pure["date"] or ai.get("date"),
        "values": merged,
        "raw": raw,
        "notes": ai.get("notes"),
    }


def missing_fields(values: dict) -> list[str]:
    return [f for f in ALL_FIELDS if f not in values]
