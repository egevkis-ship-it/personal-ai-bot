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

from app.config import settings

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)


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


@dataclass
class PlannedDay:
    """One training day from a parsed plan."""
    day_label: str                          # "Понедельник", "2026-05-26", "День 1"
    focus_label: str | None = None          # "Грудь / Трицепс"
    exercises: list[PlannedExercise] = field(default_factory=list)


# ──────────────────────────────── plan parser ─────────────────────────────────

_PLAN_SYSTEM = """\
Ты парсер тренировочных планов. Получаешь произвольный текст — еженедельный шаблон, \
даты, периоды, просто список дней — и возвращаешь JSON.

Формат ответа — ТОЛЬКО JSON, никакого другого текста:
{
  "days": [
    {
      "day_label": "Понедельник",          // или "2026-05-26", "День 1" — как в тексте
      "focus_label": "Грудь / Трицепс",    // null если не указано
      "exercises": [
        {
          "name": "Жим штанги лёжа",
          "target_sets": 4,
          "target_reps_min": 8,
          "target_reps_max": 12,
          "target_weight": 80.0,           // null если не указано
          "reps_text": null,               // "AMRAP", "до отказа" — если указано
          "notes": null,
          "superset_group": null           // "A", "B" — если суперсет
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
- Суперсеты: помечай superset_group "A", "B"... для упражнений в паре
- Если день = "Отдых" или пустой — добавляй с пустым exercises:[]
- Не добавляй пояснений, только JSON
"""


async def parse_plan_text(text: str) -> list[PlannedDay]:
    """Parse free-form plan text → list of PlannedDay."""
    try:
        resp = await _anthropic.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            system=_PLAN_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        raw = resp.content[0].text.strip()
        # strip possible markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        data: dict[str, Any] = json.loads(raw)
        days: list[PlannedDay] = []
        for d in data.get("days", []):
            exercises = [
                PlannedExercise(
                    name=e["name"],
                    target_sets=e.get("target_sets"),
                    target_reps_min=e.get("target_reps_min"),
                    target_reps_max=e.get("target_reps_max"),
                    target_weight=e.get("target_weight"),
                    reps_text=e.get("reps_text"),
                    notes=e.get("notes"),
                    superset_group=e.get("superset_group"),
                )
                for e in d.get("exercises", [])
            ]
            days.append(PlannedDay(
                day_label=d.get("day_label", ""),
                focus_label=d.get("focus_label"),
                exercises=exercises,
            ))
        return days
    except Exception as exc:
        log.error("parse_plan_text error: %s", exc)
        return []


# ──────────────────────────── set fallback parser ─────────────────────────────

_SET_SYSTEM = """\
Ты парсер одного подхода в тренировке. Пользователь пишет на русском или смеси.
Верни JSON с полями (все необязательные кроме exercise_name):
{
  "exercise_name": "Жим штанги лёжа",
  "weight_kg": 80.0,
  "reps": 10,
  "reps_text": null,
  "duration_seconds": null,
  "is_warmup": false,
  "is_failure": false
}
- Если несколько подходов — верни массив объектов
- "до отказа" / "AMRAP" → is_failure:true, reps_text:"до отказа"
- Планка/велосипед + секунды/минуты → duration_seconds (секунды)
- Только JSON, никакого текста вокруг
"""


async def parse_set_text_ai(
    text: str,
    exercise_hint: str | None = None,
) -> list[dict[str, Any]]:
    """AI fallback for set parsing. Returns list of raw dicts."""
    user_msg = text
    if exercise_hint:
        user_msg = f"Упражнение (подсказка): {exercise_hint}\nЗапись: {text}"
    try:
        resp = await _anthropic.messages.create(
            model="claude-opus-4-7",
            max_tokens=512,
            system=_SET_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = [parsed]
        return parsed
    except Exception as exc:
        log.error("parse_set_text_ai error: %s", exc)
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
