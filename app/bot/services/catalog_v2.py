"""
Merged exercise catalog (DB brief).

Primary source: the curated `data/exercise_catalog_v2.json` (109 exercises, 14
detailed muscle groups, public-domain images — Free Exercise DB). On top of it we
MERGE the legacy `EXERCISE_LIBRARY` so nothing recorded in history is orphaned:

- an old exercise whose canonical/alias overlaps a v2 entry → its names are folded
  into that v2 entry's aliases (old history strings still resolve to the v2 canonical);
- an old exercise with no overlap is kept as its own catalog entry, with its old
  `muscle_group` remapped into the new 14-group taxonomy.

Exposes: GROUPS (key→ru label), GROUP_ORDER, CATALOG (key→entry) and NAME_INDEX
(lowercased name/alias → key). Display labels come from `_meta.groups`, so the old
duplicate-label bug (biceps/triceps→«Руки», abs/core→«Пресс») is gone.
"""
from __future__ import annotations

import json
import os

from app.modules.fitness.exercise_normalizer import EXERCISE_LIBRARY

_V2_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "exercise_catalog_v2.json"))

# Legacy muscle_group → new taxonomy. Only applied to old-only (unmerged) exercises.
_GROUP_REMAP = {"legs": "quads", "posterior_chain": "hamstrings"}

# ── W3-6: input mode per exercise ───────────────────────────────────────────
# The set-entry UI has three modes: 'time' (мин:сек), 'bodyweight' (повторы only),
# 'strength' (вес×повторы). classify_input() derives the mode from the exercise's
# name/aliases + muscle_group so no exercise gets an absurd input (e.g. cardio as
# 20×10, or an assisted-machine pull-up as bodyweight). The materialised `input`
# field in the catalog JSON wins over this classifier when present.

# Loaded / machine movements → weight×reps. Checked BEFORE the bodyweight rule so
# «Подтягивания в гравитроне» (assisted machine, has a counterweight) is strength.
_WEIGHT_INDICATORS = (
    "штанг", "гантел", "гир", "блок", "кроссовер", "тренаж", "смит", "хаммер",
    "с весом", "весом", "жиме ногами", "в жиме", "ассистир", "гравитрон", "гакк",
    "рычаж", "паллов",
)
# Genuinely bodyweight movements (reps, no external load) — only when no weight
# indicator is present.
_BODYWEIGHT_PATTERNS = (
    "подтягиван", "отжиман", "брусь", "брусья", "скручиван", "складк",
    "касания носк", "подъём ног", "подъем ног", "скалолаз", "мёртвый жук",
    "мертвый жук", "велосипед", "запрыгив", "берпи", "прогулка выпад",
    "русские скручивания", "гиперэкстенз", "дровосек",
)


def classify_input(canonical_ru: str, aliases, muscle_group: str | None) -> str:
    """Return the input mode: 'time' | 'bodyweight' | 'strength'."""
    text = (canonical_ru + " " + " ".join(aliases or [])).lower()
    if (muscle_group or "") == "cardio":
        return "time"
    if "планк" in text:                                    # planks / timed holds
        return "time"
    if any(w in text for w in _WEIGHT_INDICATORS):         # loaded / machine → weight
        return "strength"
    if any(w in text for w in _BODYWEIGHT_PATTERNS):       # bodyweight reps
        return "bodyweight"
    return "strength"


def _build() -> tuple[dict, list, dict, dict]:
    with open(_V2_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    meta = raw["_meta"]
    groups: dict = dict(meta["groups"])
    group_order: list = list(meta["group_order"])

    catalog: dict = {}        # key → {canonical_ru, muscle_group, aliases:set, image, image_end, fedb_id}
    v2_name_index: dict = {}  # lowercased name/alias → v2 key (for old↔v2 overlap detection)
    for k, it in raw["exercises"].items():
        aliases = {a.strip().lower() for a in it.get("aliases", []) if a}
        aliases.add(it["canonical_ru"].strip().lower())
        # W3-6: input mode — materialised `input` in the JSON wins, else classify.
        inp = it.get("input") or classify_input(it["canonical_ru"], it.get("aliases", []), it["muscle_group"])
        catalog[k] = {"canonical_ru": it["canonical_ru"], "muscle_group": it["muscle_group"],
                      "aliases": aliases, "image": it.get("image"), "image_end": it.get("image_end"),
                      "fedb_id": it.get("fedb_id"), "input": inp}
        for nm in aliases:
            v2_name_index.setdefault(nm, k)

    merged = 0
    for ok, oit in EXERCISE_LIBRARY.items():
        onames = {oit["canonical_ru"].strip().lower()}
        onames.update(a.strip().lower() for a in oit.get("aliases", []) if a)
        match = next((v2_name_index[n] for n in onames if n in v2_name_index), None)
        if match:                                   # same exercise → fold old names in as aliases
            catalog[match]["aliases"].update(onames)
            merged += 1
        elif ok not in catalog:                     # old-only → keep, remap group, no image
            mg = _GROUP_REMAP.get(oit.get("muscle_group"), oit.get("muscle_group"))
            catalog[ok] = {"canonical_ru": oit["canonical_ru"], "muscle_group": mg,
                           "aliases": set(onames), "image": None, "image_end": None, "fedb_id": None,
                           "input": classify_input(oit["canonical_ru"], list(onames), mg)}

    name_index: dict = {}
    for k, it in catalog.items():
        name_index[it["canonical_ru"].strip().lower()] = k
        for a in it["aliases"]:
            name_index.setdefault(a, k)
    return groups, group_order, catalog, name_index


GROUPS, GROUP_ORDER, CATALOG, NAME_INDEX = _build()


def search(query: str, limit: int = 8, fuzzy: bool = False) -> list[dict]:
    """Rank catalog entries against a query (W3-2). Matching is token-substring AND:
    every whitespace token of the query must appear as a substring of the canonical
    name OR any alias (case-insensitive, any order), so a word from the MIDDLE of a
    name matches (e.g. «гравитрон» → «Подтягивания в гравитроне», «подтягив грав» too).
    Ranking: exact > prefix > whole-query substring > all-tokens-substring.

    fuzzy=True adds a weak any-token tier so an unknown/typo'd query still yields the
    closest candidates (used by the DB-7 name-matching dialog, not the strict picker)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    tokens = [t for t in q.split() if t]
    scored: list[tuple] = []
    for k, it in CATALOG.items():
        names = it["aliases"] | {it["canonical_ru"].strip().lower()}
        blob = " ".join(names)   # canonical + every alias, for token/substring matching
        if any(n == q for n in names):
            s = 100
        elif any(n.startswith(q) for n in names):
            s = 80
        elif q in blob:                                       # whole query is contiguous somewhere
            s = 65
        elif tokens and all(t in blob for t in tokens):       # every token matches (AND, any order)
            s = 50
        elif fuzzy and tokens and any(t in blob for t in tokens):   # any token → closest candidates
            s = 20
        else:
            s = 0
        if s:
            scored.append((s, it["canonical_ru"], k, it))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"exercise_key": k, "name": it["canonical_ru"], "muscle_group": it["muscle_group"],
             "image": it.get("image"), "type": it.get("input")} for _, _, k, it in scored[:limit]]
