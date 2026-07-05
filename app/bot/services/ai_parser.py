"""
AI-based parsers using Claude.

1. parse_plan_text(text) → list[PlannedDay]
   Parses free-form workout plan text (weekly, by-date, by-period etc.)

2. parse_set_text_ai(text, exercise_name) → SetResult | None
   Fallback when pure-text parser can't figure out the set.

3. transcribe_voice(ogg_bytes) → str
   Whisper voice-to-text.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic
import openai

from app.bot.services.catalog_v2 import CATALOG
from app.config import settings

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)

# Valid current model (the old "claude-opus-4-7" id no longer resolves and made
# every AI call 404 — silently, because callers swallowed the exception).
PARSER_MODEL = "claude-opus-4-8"


# REC-3: give the parser the catalog's canonical names so it can fold obvious
# phrasing variants (angle/grip/stance/side/declension) to the EXACT canon — fewer
# manual confirmations. Built from CATALOG at import time (no hard-coding, so it
# tracks REC-1 edits). INVARIANT: an unknown/uncertain movement must pass through
# VERBATIM (→ _resolve_name returns resolved:false → the DB-7 dialog); the parser
# must never swap it for a merely-similar entry.
def _catalog_hint() -> str:
    names = sorted({it["canonical_ru"] for it in CATALOG.values()})
    return (
        "\n\nСПРАВОЧНИК КАНОНИЧЕСКИХ НАЗВАНИЙ (из базы упражнений):\n"
        + "\n".join(names)
        + "\n\nПРАВИЛО ИМЁН: если упражнение из текста ЯВНО одно из списка выше (отличается только "
        "углом/хватом/стойкой/стороной/склонением/сокращением) — верни ТОЧНО это каноническое имя "
        "из списка. Если не уверен, или движения в списке НЕТ, или оно иное по механике/оборудованию "
        "— верни имя КАК В ТЕКСТЕ (дословно), НЕ выдумывай и НЕ подгоняй под похожее."
    )


_CATALOG_HINT = _catalog_hint()


class PlanParseError(Exception):
    """The AI plan-parse call itself failed (model/network/auth), as opposed to
    the model returning text we couldn't turn into days. Lets callers tell a real
    outage (→ HTTP 502 with the cause) apart from an empty/garbled parse (→ 422)."""


# ────────────────────────────── data models ───────────────────────────────────

@dataclass
class PlannedExercise:
    name: str
    target_sets: int | None = None
    target_reps_min: int | None = None
    target_reps_max: int | None = None
    target_weight: float | None = None
    reps_text: str | None = None       # "AMRAP", "до отказа"
    notes: str | None = None
    superset_group: str | None = None
    set_groups: list | None = None     # REC-4: weight tiers, e.g. [{sets,reps_min,reps_max,weight}]
    target_duration_seconds: int | None = None   # REC-5: cardio/time goal


@dataclass
class PlannedDay:
    """One training day from a parsed plan."""
    day_label: str                          # "Понедельник", "2026-05-26", "День 1"
    focus_label: str | None = None          # "Грудь / Трицепс"
    notes: str | None = None                # day-level free-text comment
    exercises: list[PlannedExercise] = field(default_factory=list)


# ──────────────────────────────── plan parser ─────────────────────────────────

_PLAN_SYSTEM = """\
Ты парсер тренировочных планов. Получаешь произвольный текст — еженедельный шаблон, \
даты, периоды, просто список дней с заметками — и возвращаешь JSON.

Формат ответа — ТОЛЬКО JSON, никакого другого текста:
{
  "days": [
    {
      "day_label": "Понедельник",
      "focus_label": "Грудь / Трицепс",
      "notes": "Усиленный день. Перед жимом 5 мин разминка.",
      "exercises": [
        {
          "name": "Жим штанги лёжа",
          "target_sets": 4,
          "target_reps_min": 8,
          "target_reps_max": 12,
          "target_weight": 80.0,
          "reps_text": null,
          "notes": "Постараться добавить 2.5кг к рабочему. Пауза в нижней точке 2 сек.",
          "superset_group": null,
          "set_groups": null,
          "target_duration_seconds": null
        }
      ]
    }
  ]
}

Правила:
- Если вес не указан — target_weight: null
- "4×10" → target_sets:4, target_reps_min:10, target_reps_max:10
- "4×8-12" → target_sets:4, target_reps_min:8, target_reps_max:12
- "AMRAP" / "до отказа" → reps_text:"до отказа", target_reps_min:null
- КАРДИО / ВРЕМЯ / РАЗМИНКА по времени ("велотренажёр 7-10 мин", "разминка 5 мин", "планка 60 сек", \
"бег 20 минут") → target_duration_seconds В СЕКУНДАХ (минуты×60), а target_sets/target_reps_*/ \
target_weight = null (НЕ придумывай подходы×повторы для кардио). Диапазон "7-10 мин" → возьми \
верхнюю границу (600). Название кардио — как в тексте (велотренажёр/беговая дорожка/эллипс…).
- ЯРУСЫ ВЕСА — несколько групп подходов с РАЗНЫМ весом/повторами в ОДНОЙ строке \
("2×12 на 12.5 кг + 2×15 на 10 кг", "1×10-12 на 15 + 3×10-12 на 10", пирамида, \
"тяжёлые в начале, пампинг в конце") → эмить массив "set_groups", по объекту на ярус: \
[{"sets":2,"reps_min":12,"reps_max":12,"weight":12.5},{"sets":2,"reps_min":15,"reps_max":15,"weight":10}]. \
Плоские target_* при этом заполни из ПЕРВОГО яруса (target_sets/reps/weight = первый ярус). \
ОДНОЯРУСНЫЕ строки ("4×10, 80 кг") — set_groups:null, только плоские target_*. Не плоди \
set_groups без нужды.
- Суперсеты: помечай superset_group "A", "B"... для упражнений в паре
- День отдыха (явный «Отдых»/«rest»/«выходной»/«день отдыха» ИЛИ день недели без тренировки): \
focus_label:"Отдых", exercises:[].
- Если текст описывает неделю по дням недели (Пн–Вс), верни ВСЕ упомянутые дни недели: \
где тренировка указана — с упражнениями; где её нет/пусто — день отдыха (focus_label:"Отдых", \
exercises:[]). Не оставляй «дыр» между тренировочными днями.
- КОММЕНТАРИИ: любой свободный текст после упражнения или дня (после ":", "//", "—", или просто \
текст без чисел рядом со счётом) клади в notes соответствующего уровня. \
Примеры: "Комментарий: сидушка 1 дырка", "// сидушка 2 дырки", "не до отказа", \
"в отказ", "если хуже чувствуешь — снизить вес", "опционально". \
Не путай notes с упражнением — это пояснение.
- notes для дня = общий комментарий на тренировку (разминка, цель)
- notes для упражнения = комментарий именно к этому движению
- Не добавляй пояснений, только JSON
"""


def _extract_json(raw: str) -> str:
    """Best-effort JSON extraction from Claude reply.

    Handles:
      - markdown ```json fences
      - text before/after the JSON
      - truncated tail by trimming back to the last well-balanced '}' / ']'.
    """
    s = raw.strip()
    if s.startswith("```"):
        # ```json\n{...}\n``` or just ```\n{...}\n```
        try:
            s = s.split("\n", 1)[1].rsplit("```", 1)[0]
        except Exception:
            pass
    # Find first { or [
    start_obj = s.find("{")
    start_arr = s.find("[")
    starts = [x for x in (start_obj, start_arr) if x >= 0]
    if not starts:
        return s
    start = min(starts)
    s = s[start:]
    # Walk and find matching close
    depth = 0
    in_str = False
    esc = False
    last_good = -1
    for i, ch in enumerate(s):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                last_good = i + 1
                break
    if last_good > 0:
        return s[:last_good]
    return s


async def parse_plan_text(text: str) -> list[PlannedDay]:
    """Parse free-form plan text → list of PlannedDay.

    Raises PlanParseError if the Anthropic call fails (so the web layer can return
    a 502 with the real cause). Returns [] only when the model replied but its
    output couldn't be parsed into days (→ the caller shows "couldn't parse")."""
    try:
        resp = await _anthropic.messages.create(
            model=PARSER_MODEL,
            max_tokens=8192,
            system=_PLAN_SYSTEM + _CATALOG_HINT,
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:
        # Do NOT swallow: a model/network/auth failure must surface, not look like
        # an unparseable plan. Log the real error (Sentry picks this up in phase A).
        log.error("parse_plan_text: Anthropic call failed: %s", exc, exc_info=True)
        raise PlanParseError(str(exc)) from exc

    raw = resp.content[0].text
    candidate = _extract_json(raw)
    try:
        data: dict[str, Any] = json.loads(candidate)
    except json.JSONDecodeError as je:
        log.warning(
            "parse_plan_text: JSON broken at %d:%d (%s). Raw len=%d. Snippet: %r",
            je.lineno, je.colno, je.msg, len(raw), candidate[max(0, je.pos-80):je.pos+80],
        )
        return []
    days: list[PlannedDay] = []
    for d in data.get("days", []):
        exercises = [
            PlannedExercise(
                name=e.get("name", "?"),
                target_sets=e.get("target_sets"),
                target_reps_min=e.get("target_reps_min"),
                target_reps_max=e.get("target_reps_max"),
                target_weight=e.get("target_weight"),
                reps_text=e.get("reps_text"),
                notes=e.get("notes"),
                superset_group=e.get("superset_group"),
                set_groups=e.get("set_groups"),
                target_duration_seconds=e.get("target_duration_seconds"),
            )
            for e in d.get("exercises", [])
            if e.get("name")
        ]
        days.append(PlannedDay(
            day_label=d.get("day_label", ""),
            focus_label=d.get("focus_label"),
            notes=d.get("notes"),
            exercises=exercises,
        ))
    return days


# ──────────────────────── logged (past) workouts parser ───────────────────────
# HIST-2: parse one or several PAST workouts pasted as free text → structured
# workouts with a date and ACTUAL logged sets (weight×reps), not future targets.

_LOG_SYSTEM = """\
Ты парсер УЖЕ ВЫПОЛНЕННЫХ (прошлых) тренировок. Получаешь произвольный текст с одной \
или НЕСКОЛЬКИМИ тренировками из прошлого и возвращаешь JSON с ФАКТИЧЕСКИ сделанными подходами.

Формат ответа — ТОЛЬКО JSON, без другого текста:
{
  "workouts": [
    {
      "date": "2026-06-15",
      "date_text": "15 июня",
      "focus_label": "Грудь / Трицепс",
      "notes": null,
      "exercises": [
        {
          "name": "Жим штанги лёжа",
          "sets": [
            {"weight_kg": 80.0, "reps": 10, "reps_text": null, "duration_seconds": null, "is_warmup": false, "is_failure": false},
            {"weight_kg": 82.5, "reps": 8, "reps_text": null, "duration_seconds": null, "is_warmup": false, "is_failure": false}
          ]
        }
      ]
    }
  ]
}

Правила:
- ДАТЫ: если в блоке есть КАЛЕНДАРНАЯ дата (2026-06-15, 15.06.2026, 15.06.26) — переведи её в \
ISO "YYYY-MM-DD" в поле "date". Ты НЕ знаешь сегодняшнюю дату, поэтому относительные/неполные \
даты ("вчера", "понедельник", "15 июня" без года) НЕ вычисляй: оставь "date": null, а исходную \
запись положи в "date_text". Всегда заполняй "date_text" тем, как дата написана (или null если её нет).
- Каждый смысловой блок (своя дата ИЛИ явный разделитель тренировки) = отдельный элемент "workouts".
- ПОДХОДЫ — фактические: "80×10" → {"weight_kg":80,"reps":10}; "80 на 10", "80 10" → то же; \
"80×10, 82×8, 80×8" → три подхода; "3×10×80" или "3 по 10 на 80" → три одинаковых подхода 80×10; \
"планка 60 сек" → {"duration_seconds":60}; без веса ("подтягивания 12,10,8") → weight_kg:null, reps по числам.
- "разминка"/"разм" у подхода → is_warmup:true. "до отказа"/"в отказ"/"AMRAP" → is_failure:true И reps_text:"до отказа".
- Имя упражнения — как в тексте (нормализуют позже). Незнакомые числа без упражнения игнорируй.
- focus_label — если в блоке указан фокус/группа ("Грудь", "Ноги"), иначе null.
- Если в тексте только подходы без явных тренировок — верни один workout с date:null.
- Никаких пояснений, только JSON.
"""


async def parse_logged_workouts_text(text: str) -> list[dict[str, Any]]:
    """Parse free-text PAST workouts → list of dicts:
    {date(ISO|None), date_text, focus_label, notes, exercises:[{name, sets:[{...}]}]}.
    Raises PlanParseError on a model/network/auth failure (→ 502); returns [] when
    the model replied but the output couldn't be parsed (→ 422)."""
    try:
        resp = await _anthropic.messages.create(
            model=PARSER_MODEL,
            max_tokens=8192,
            system=_LOG_SYSTEM + _CATALOG_HINT,
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:
        log.error("parse_logged_workouts_text: Anthropic call failed: %s", exc, exc_info=True)
        raise PlanParseError(str(exc)) from exc
    raw = resp.content[0].text
    candidate = _extract_json(raw)
    try:
        data: dict[str, Any] = json.loads(candidate)
    except json.JSONDecodeError as je:
        log.warning("parse_logged_workouts_text: JSON broken (%s). Raw len=%d", je.msg, len(raw))
        return []
    out: list[dict[str, Any]] = []
    workouts = data.get("workouts")
    for w in workouts if isinstance(workouts, list) else []:
        if not isinstance(w, dict):
            continue
        exercises = []
        ex_list = w.get("exercises")
        for e in ex_list if isinstance(ex_list, list) else []:
            if not isinstance(e, dict) or not e.get("name"):
                continue
            sets = []
            sets_raw = e.get("sets")
            for st in sets_raw if isinstance(sets_raw, list) else []:
                if not isinstance(st, dict):
                    continue
                if st.get("weight_kg") is None and st.get("reps") is None \
                        and st.get("duration_seconds") is None and not st.get("reps_text"):
                    continue
                sets.append({
                    "weight_kg": st.get("weight_kg"), "reps": st.get("reps"),
                    "reps_text": st.get("reps_text"), "duration_seconds": st.get("duration_seconds"),
                    "is_warmup": bool(st.get("is_warmup")), "is_failure": bool(st.get("is_failure")),
                })
            if sets:
                exercises.append({"name": e["name"], "sets": sets})
        if exercises:
            out.append({
                "date": w.get("date"), "date_text": w.get("date_text"),
                "focus_label": w.get("focus_label"), "notes": w.get("notes"),
                "exercises": exercises,
            })
    return out


# ──────────────────────────── set fallback parser ─────────────────────────────

_SET_SYSTEM = """\
Ты парсер подходов в тренировке. Пользователь пишет ИЛИ диктует голосом \
(текст уже распознан Whisper, могут быть ошибки распознавания).

Верни МАССИВ JSON-объектов — по объекту на каждый ПОДХОД (set), не на каждое число.

Поля объекта (все опциональны кроме exercise_name):
{
  "exercise_name": "Жим штанги лёжа",
  "weight_kg": 80.0,
  "reps": 10,
  "reps_text": null,
  "duration_seconds": null,
  "is_warmup": false,
  "is_failure": false
}

КРИТИЧНО — отличай НОМЕР ПОДХОДА от КОЛИЧЕСТВА ПОДХОДОВ:

  "первый подход X кг на Y повт" / "1 подход X кг на Y повт" / "1-й подход" \
    = ОДИН подход (номер 1) с X кг и Y повт. → 1 объект в массиве.

  "2 подхода X кг на Y повт" / "три подхода X на Y" / "ещё 2 подхода X×Y" \
    = N ИДЕНТИЧНЫХ подходов. → N одинаковых объектов в массиве.

  "2 подход" БЕЗ окончания "-а" — это разговорное "ВТОРОЙ подход" (номер), \
    то есть 1 подход. Но "2 подхода" (с -а) — это КОЛИЧЕСТВО = 2.

ПРИМЕРЫ:
  Вход:  "1 подход 80 кг на 12 повт. 2 подхода 75 кг на 12 повт."
  Выход (массив из 3 объектов):
    [{w:80,r:12}, {w:75,r:12}, {w:75,r:12}]
    (один подход с 80, плюс ДВА идентичных подхода с 75)

  Вход:  "первый подход 80 на 10, второй 75 на 8"
  Выход: [{w:80,r:10}, {w:75,r:8}]   ← это номера, два разных подхода

  Вход:  "3 подхода 100 кг по 5"
  Выход: [{w:100,r:5}, {w:100,r:5}, {w:100,r:5}]   ← три идентичных

ДРУГИЕ КРИТИЧНЫЕ ПРАВИЛА:
- "12 повт 25 кг" = один подход, НЕ ДВА (12 ≠ подход, это повторения)
- Голосовая речь часто такого формата: "[номер] подход [число] повт [число] кг"
- Если вес не назван — weight_kg:null; если повторения не названы — reps:null
- "до отказа" / "AMRAP" → is_failure:true, reps_text:"до отказа"
- Планка/велосипед + секунды/минуты → duration_seconds (секунды)

ИСПРАВЛЯЙ очевидные ошибки распознавания речи:
- "сжим" → "жим"
- "кандели" → "гантели"
- "подьёмы"/"падъёмы" → "подъёмы"
- "штанги" / "штанга" нормально
- "стоя"/"сидя"/"под углом" — это уточнения, оставляй

Используй стандартные имена упражнений (на русском, с заглавной буквы):
"Жим штанги лёжа", "Жим гантелей под углом", "Тяга верхнего блока", \
"Приседания со штангой", "Становая тяга", "Подъём гантелей на бицепс", "Молот", \
"Французский жим", "Махи в стороны", "Подтягивания", "Отжимания на брусьях" и т.п.

Только JSON-массив. Никакого текста вокруг. Никаких пояснений.
"""


async def parse_set_text_ai(
    text: str,
    exercise_hint: str | None = None,
) -> list[dict[str, Any]]:
    """AI fallback for set parsing. Returns list of raw dicts (one per set)."""
    user_msg = text
    if exercise_hint:
        user_msg = f"Подсказка (предыдущее упражнение): {exercise_hint}\n\nРаспознанный текст: {text}"
    try:
        resp = await _anthropic.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=_SET_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        # Find first '[' or '{' — model may wrap with prose
        i_arr = raw.find("[")
        i_obj = raw.find("{")
        starts = [x for x in (i_arr, i_obj) if x >= 0]
        if starts:
            raw = raw[min(starts):]
            # Trim trailing prose after last ']' or '}'
            end = max(raw.rfind("]"), raw.rfind("}"))
            if end > 0:
                raw = raw[:end + 1]
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = [parsed]
        return parsed
    except Exception as exc:
        log.error("parse_set_text_ai error: %s. Raw=%r", exc, locals().get("raw","")[:300])
        return []


# ──────────────────────────────── voice → text ────────────────────────────────

async def transcribe_voice(ogg_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe voice message (OGG/OPUS) via Whisper."""
    import io
    try:
        audio_file = io.BytesIO(ogg_bytes)
        audio_file.name = filename
        result = await _openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ru",
        )
        return result.text.strip()
    except Exception as exc:
        log.error("transcribe_voice error: %s", exc)
        return ""
