"""
Exercise catalog — hybrid normalization:

  1. Static library (Russian aliases) ported from the legacy
     `app/modules/fitness/exercise_normalizer.py`.
  2. Bilingual extension (English aliases added on top).
  3. Per-user cache of AI-resolved aliases in DB table `exercise_aliases`.
  4. AI fallback via Claude Sonnet for unknown names.

Public:
  normalize_exercise(name)          → dict {canonical, muscle_group, source}
  resolve_or_register(name)         → async; uses DB cache + AI fallback
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

import anthropic
from sqlalchemy import text

from app.config import settings
from app.db.engine import get_session
from app.modules.fitness.exercise_normalizer import (
    EXERCISE_LIBRARY,
    alias_index,
    clean_text,
    normalize_exercise_name,
)

log = logging.getLogger(__name__)

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


# ───────────────────────────── english aliases ──────────────────────────────
# Extends the static library without re-declaring every entry.
# Map of canonical_ru → list of english aliases.
_EN_ALIASES = {
    "Жим штанги лёжа": ["bench press", "barbell bench press", "flat bench press"],
    "Жим штанги под углом": ["incline barbell press", "incline bench press"],
    "Жим гантелей под углом": ["incline dumbbell press"],
    "Жим гантелей лёжа": ["dumbbell bench press", "flat dumbbell press"],
    "Жим на грудь в тренажёре": ["chest press machine", "machine chest press"],
    "Сведение рук на грудь в кроссовере": ["cable chest fly", "cable fly", "cable crossover"],
    "Разводка гантелей лёжа": ["dumbbell fly", "dumbbell flyes"],
    "Отжимания": ["push ups", "pushups"],
    "Отжимания на брусьях": ["dips", "bar dips"],
    "Подтягивания": ["pull ups", "pullups", "chin ups", "chinups"],
    "Тяга верхнего блока": ["lat pulldown", "pulldown"],
    "Тяга штанги в наклоне": ["barbell row", "bent over row", "bb row"],
    "Тяга гантели одной рукой": ["one arm dumbbell row", "single arm row", "db row"],
    "Тяга нижнего блока сидя": ["seated cable row", "cable row"],
    "Гиперэкстензия": ["hyperextension", "back extension"],
    "Становая тяга": ["deadlift", "conventional deadlift"],
    "Румынская тяга": ["romanian deadlift", "rdl"],
    "Приседания со штангой": ["squat", "back squat", "barbell squat"],
    "Фронтальные приседания": ["front squat"],
    "Жим ногами": ["leg press"],
    "Разгибания ног в тренажёре": ["leg extension"],
    "Сгибания ног лёжа": ["lying leg curl", "leg curl"],
    "Сгибания ног сидя": ["seated leg curl"],
    "Подъёмы на носки стоя": ["standing calf raise", "calf raise"],
    "Подъёмы на носки сидя": ["seated calf raise"],
    "Жим штанги стоя": ["overhead press", "ohp", "military press"],
    "Жим гантелей сидя": ["seated dumbbell press", "db shoulder press"],
    "Махи в стороны стоя": ["lateral raise", "side raise"],
    "Махи в стороны сидя": ["seated lateral raise"],
    "Махи в наклоне": ["bent over lateral raise", "rear delt fly"],
    "Подъём штанги на бицепс": ["barbell curl", "bb curl"],
    "Подъём гантелей на бицепс": ["dumbbell curl", "db curl"],
    "Молотки": ["hammer curl"],
    "Французский жим": ["skull crusher", "lying triceps extension"],
    "Жим узким хватом": ["close grip bench press"],
    "Разгибания на трицепс на блоке": ["triceps pushdown", "cable pushdown"],
    "Планка": ["plank"],
    "Скручивания": ["crunches", "sit ups"],
    "Подъёмы ног в висе": ["hanging leg raises", "knee raises"],
}


@lru_cache(maxsize=1)
def _en_alias_index() -> dict[str, str]:
    """key = cleaned alias text, value = canonical_ru."""
    out = {}
    for canonical, aliases in _EN_ALIASES.items():
        for a in aliases:
            out[clean_text(a)] = canonical
    return out


def normalize_exercise(name: str | None) -> dict:
    """Synchronous normalization via static library + EN aliases.

    Returns: {canonical: str, muscle_group: str | None, source: str}.
    `source` ∈ {"exact","contains","english","unknown"}.
    """
    if not name:
        return {"canonical": "", "muscle_group": None, "source": "empty"}

    # 1. RU static lib
    r = normalize_exercise_name(name)
    if r["confidence"] >= 0.8:
        return {
            "canonical": r["canonical_ru"],
            "muscle_group": r.get("muscle_group"),
            "source": "exact" if r["source"] == "exact_alias" else "contains",
        }

    # 2. EN aliases
    cleaned = clean_text(name)
    en_idx = _en_alias_index()
    if cleaned in en_idx:
        canonical = en_idx[cleaned]
        # Look up muscle group from EXERCISE_LIBRARY
        for key, item in EXERCISE_LIBRARY.items():
            if item["canonical_ru"] == canonical:
                return {
                    "canonical": canonical,
                    "muscle_group": item.get("muscle_group"),
                    "source": "english",
                }
        return {"canonical": canonical, "muscle_group": None, "source": "english"}

    # Partial EN match
    for alias_clean, canonical in en_idx.items():
        if len(alias_clean) >= 5 and (alias_clean in cleaned or cleaned in alias_clean):
            for key, item in EXERCISE_LIBRARY.items():
                if item["canonical_ru"] == canonical:
                    return {
                        "canonical": canonical,
                        "muscle_group": item.get("muscle_group"),
                        "source": "english_partial",
                    }

    return {"canonical": name, "muscle_group": None, "source": "unknown"}


# ───────────────────────────── DB-cached AI fallback ────────────────────────

async def _lookup_alias_cache(alias_text: str) -> dict | None:
    cleaned = clean_text(alias_text)
    async with get_session() as s:
        r = await s.execute(
            text("SELECT canonical, muscle_group FROM exercise_aliases WHERE alias_clean = :a LIMIT 1"),
            {"a": cleaned},
        )
        row = r.mappings().first()
        return dict(row) if row else None


async def _save_alias(alias_text: str, canonical: str, muscle_group: str | None, source: str) -> None:
    cleaned = clean_text(alias_text)
    async with get_session() as s:
        await s.execute(
            text("""
                INSERT INTO exercise_aliases (alias_text, alias_clean, canonical, muscle_group, source)
                VALUES (:a, :ac, :c, :m, :s)
                ON CONFLICT (alias_clean) DO NOTHING
            """),
            {"a": alias_text, "ac": cleaned, "c": canonical, "m": muscle_group, "s": source},
        )


_AI_NORMALIZE_SYSTEM = """\
Ты нормализатор названий упражнений из тренажерного зала. На вход — текст с упражнением \
(русский, английский, разговорный, после голосового распознавания могут быть ошибки). \
На выход — строгий JSON:

{"canonical_ru":"Жим штанги лёжа","muscle_group":"chest"}

Правила:
- canonical_ru — короткое каноническое РУССКОЕ название с заглавной буквы и буквой "ё"
- muscle_group ∈ {chest, back, legs, shoulders, biceps, triceps, core, glutes, calves, forearms, cardio}

ИСПРАВЛЯЙ ошибки распознавания речи (Whisper):
- "сжим"/"зжим" → "жим"
- "кандели"/"гантэли" → "гантели"
- "падъёмы"/"подьёмы" → "подъёмы"
- "тэга" → "тяга"
- "штанке" → "штанги"
- Подобные искажения — догадайся

Типичные канонические формы:
- "Жим штанги лёжа", "Жим штанги под углом", "Жим штанги стоя"
- "Жим гантелей лёжа", "Жим гантелей под углом", "Жим гантелей сидя"
- "Тяга верхнего блока", "Тяга нижнего блока", "Тяга штанги в наклоне"
- "Приседания со штангой", "Становая тяга", "Румынская тяга"
- "Подъём штанги на бицепс", "Подъём гантелей на бицепс", "Молот"
- "Французский жим", "Разгибания на трицепс на блоке"
- "Махи в стороны стоя", "Махи в стороны сидя", "Жим штанги стоя"
- "Подтягивания", "Отжимания", "Отжимания на брусьях", "Планка"

Если упражнение реально неопределимо — canonical_ru = исходный текст, muscle_group = null.
Только JSON, никаких пояснений.
"""


async def _ai_normalize(name: str) -> dict | None:
    try:
        resp = await _anthropic.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=120,
            system=_AI_NORMALIZE_SYSTEM,
            messages=[{"role": "user", "content": name}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(raw)
        return {
            "canonical": data.get("canonical_ru", name),
            "muscle_group": data.get("muscle_group"),
            "source": "ai",
        }
    except Exception as exc:
        log.error("ai_normalize error: %s", exc)
        return None


async def resolve_or_register(name: str) -> dict:
    """Full pipeline: static lib → EN → DB cache → AI → fallback.

    Returns {canonical, muscle_group, source}.
    Side-effect: cached resolutions are persisted to `exercise_aliases`.
    """
    if not name or not name.strip():
        return {"canonical": "", "muscle_group": None, "source": "empty"}

    static = normalize_exercise(name)
    if static["source"] != "unknown":
        return static

    # Try DB cache
    cached = await _lookup_alias_cache(name)
    if cached:
        return {
            "canonical": cached["canonical"],
            "muscle_group": cached.get("muscle_group"),
            "source": "db_cache",
        }

    # AI fallback
    ai = await _ai_normalize(name)
    if ai and ai["canonical"] and ai["source"] == "ai":
        try:
            await _save_alias(name, ai["canonical"], ai["muscle_group"], "ai")
        except Exception as exc:
            log.warning("save alias failed: %s", exc)
        return ai

    return {"canonical": name, "muscle_group": None, "source": "unknown"}
