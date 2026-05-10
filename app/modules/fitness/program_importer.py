from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ProgramExercise:
    exercise_name: str
    order: int | None = None
    superset_group: int | None = None
    superset_item: str | None = None
    target_sets: int | None = None
    target_reps_min: int | None = None
    target_reps_max: int | None = None
    target_reps_text: str | None = None
    target_weight_kg: float | None = None
    planned_reps: list[int] | None = None
    notes: str | None = None


@dataclass
class ProgramDay:
    day_index: int
    title: str
    focus: str | None
    exercises: list[ProgramExercise]


@dataclass
class TrainingProgram:
    title: str
    duration_type: str
    days: list[ProgramDay]
    source_type: str = "text"


VIDEO_RE = re.compile(r"\s*\(?\s*видео\s*\)?", re.IGNORECASE)
DAY_RE = re.compile(r"^\s*день\s+(\d+)\s*$", re.IGNORECASE)
LEADING_NUMBER_RE = re.compile(r"^\s*(\d+)[\.\)]\s*(.+)$")
PARAM_AT_END_RE = re.compile(
    r"(?P<body>.*?)(?:\s+)(?P<param>(?:\d+\s*[*xх×]\s*\d+(?:\s*-\s*\d+)?|\d+(?:\s*-\s*\d+){2,}|\d+\s*подхода?\s*(?:по)?\s*\d+|\d+\s*по\s*\d+))\s*$",
    re.IGNORECASE,
)
WEIGHT_RE = re.compile(r"(?P<weight>\d+(?:[.,]\d+)?)\s*(?:кг|килограмм|килограмма|килограммов)\b", re.IGNORECASE)
QUOTE_NOTE_RE = re.compile(r"\b(\d+)\s*[”\"″]\s*")


def _clean_line(line: str) -> str:
    line = (line or "").strip()
    line = VIDEO_RE.sub("", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def _strip_weight_from_name(text: str) -> tuple[str, float | None]:
    weight = None

    matches = list(WEIGHT_RE.finditer(text))
    if matches:
        m = matches[-1]
        try:
            weight = float(m.group("weight").replace(",", "."))
        except Exception:
            weight = None
        text = (text[:m.start()] + text[m.end():]).strip()

    text = re.sub(r"\s+", " ", text).strip(" ,;-")
    return text, weight


def _parse_param(param: str | None) -> dict[str, Any]:
    if not param:
        return {}

    p = param.strip().lower().replace("х", "x").replace("×", "x")
    p = re.sub(r"\s+", "", p)

    # 5*15 / 5x15 / 4*10-12
    m = re.match(r"^(\d+)[*x](\d+)(?:-(\d+))?$", p)
    if m:
        sets = int(m.group(1))
        reps_min = int(m.group(2))
        reps_max = int(m.group(3)) if m.group(3) else reps_min
        return {
            "target_sets": sets,
            "target_reps_min": reps_min,
            "target_reps_max": reps_max,
        }

    # 4по12 / 4подходапо12
    m = re.match(r"^(\d+)(?:подхода?|по)(?:по)?(\d+)$", p)
    if m:
        sets = int(m.group(1))
        reps = int(m.group(2))
        return {
            "target_sets": sets,
            "target_reps_min": reps,
            "target_reps_max": reps,
        }

    # 25-20-20-20-20-15-15
    if re.match(r"^\d+(?:-\d+){2,}$", p):
        reps = [int(x) for x in p.split("-") if x.isdigit()]
        return {
            "target_sets": len(reps),
            "planned_reps": reps,
            "notes": "planned_reps: " + "-".join(str(x) for x in reps),
        }

    return {
        "target_reps_text": param.strip(),
    }




def _extract_quote_notes(text: str) -> tuple[str, list[str]]:
    notes = []

    def repl(match: re.Match) -> str:
        value = match.group(1)
        notes.append(f'{value}”')
        return ""

    cleaned = QUOTE_NOTE_RE.sub(repl, text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, notes

def _extract_parenthetical_notes(text: str) -> tuple[str, list[str]]:
    notes = []

    def repl(match: re.Match) -> str:
        content = match.group(1).strip()
        if content and content.lower() != "видео":
            notes.append(content)
        return ""

    cleaned = re.sub(r"\(([^)]*)\)", repl, text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, notes


def _parse_exercise_line(raw_line: str, fallback_order: int) -> list[ProgramExercise]:
    line = _clean_line(raw_line)
    if not line:
        return []

    order = fallback_order

    m = LEADING_NUMBER_RE.match(line)
    if m:
        try:
            order = int(m.group(1))
        except Exception:
            order = fallback_order
        line = m.group(2).strip()

    line, paren_notes = _extract_parenthetical_notes(line)
    line, quote_notes = _extract_quote_notes(line)

    param = None
    m = PARAM_AT_END_RE.match(line)
    if m:
        line = m.group("body").strip()
        param = m.group("param").strip()

    parsed_param = _parse_param(param)

    # If there is a weight inside body, keep it as target_weight_kg.
    line, weight = _strip_weight_from_name(line)
    if weight is not None:
        parsed_param["target_weight_kg"] = weight

    notes = list(paren_notes) + list(quote_notes)
    if parsed_param.get("notes"):
        notes.append(parsed_param.pop("notes"))

    # Superset via +
    parts = [p.strip(" +") for p in line.split("+") if p.strip(" +")]
    if not parts:
        return []

    exercises = []
    is_superset = len(parts) > 1

    for idx, part in enumerate(parts):
        name = re.sub(r"\s+", " ", part).strip(" ,;-")
        if not name:
            continue

        item = None
        if is_superset:
            item = chr(ord("A") + idx)

        ex_notes = list(notes)
        if is_superset:
            ex_notes.append(f"superset_group={order}")
            ex_notes.append(f"superset_item={item}")

        exercises.append(
            ProgramExercise(
                exercise_name=name,
                order=order,
                superset_group=order if is_superset else None,
                superset_item=item,
                target_sets=parsed_param.get("target_sets"),
                target_reps_min=parsed_param.get("target_reps_min"),
                target_reps_max=parsed_param.get("target_reps_max"),
                target_reps_text=parsed_param.get("target_reps_text"),
                target_weight_kg=parsed_param.get("target_weight_kg"),
                planned_reps=parsed_param.get("planned_reps"),
                notes="; ".join(ex_notes) if ex_notes else None,
            )
        )

    return exercises


def _infer_focus(exercises: list[ProgramExercise]) -> str | None:
    names = " ".join(ex.exercise_name.lower() for ex in exercises)

    scores = {
        # “кроссовер” alone is not chest: e.g. “задняя дельта в кроссовере”.
        "грудь": ["жим штанги", "груд", "жим на груд", "сведение рук на грудь"],
        "спина": ["подтяг", "вертикальная тяга", "горизонтальная тяга", "тяга гант"],
        "плечи": ["дельт", "махи", "фронталь", "жим гантелей сидя"],
        "ноги": ["ног", "икры", "жим ногами", "сгибание ног", "разгибание ног"],
        "руки": ["бицепс", "трицепс", "скамья скотта", "брусья"],
        "пресс": ["пресс", "v склад", "v-склад"],
    }

    matched = []
    for focus, keys in scores.items():
        if any(k in names for k in keys):
            matched.append(focus)

    if not matched:
        return None

    # Keep compact but informative.
    return " / ".join(matched[:3])


def parse_training_program_text(text: str, title: str | None = None, source_type: str = "text") -> TrainingProgram:
    raw_lines = (text or "").splitlines()

    # Clean header-ish lines but keep day/exercise lines.
    lines = []
    for raw in raw_lines:
        line = _clean_line(raw)
        if not line:
            continue
        if line.lower() in {"программа", "тренировок"}:
            continue
        lines.append(line)

    days_raw: dict[int, list[str]] = {}
    current_day: int | None = None

    for line in lines:
        m = DAY_RE.match(line)
        if m:
            current_day = int(m.group(1))
            days_raw.setdefault(current_day, [])
            continue

        if current_day is not None:
            days_raw.setdefault(current_day, []).append(line)

    # Fallback: no explicit days, treat as Day 1.
    if not days_raw and lines:
        days_raw[1] = lines

    days = []
    for day_index in sorted(days_raw):
        exercises: list[ProgramExercise] = []
        fallback_order = 1

        for raw_line in days_raw[day_index]:
            parsed = _parse_exercise_line(raw_line, fallback_order=fallback_order)
            if not parsed:
                continue

            exercises.extend(parsed)
            fallback_order += 1

        # Renumber visual order continuously.
        # Superset items share the same visible base order: 5A / 5B.
        normalized = []
        visual_order = 1
        last_superset_group = None

        for ex in exercises:
            if ex.superset_group is not None:
                if ex.superset_group != last_superset_group:
                    ex.order = visual_order
                    last_superset_group = ex.superset_group
                    visual_order += 1
                else:
                    ex.order = visual_order - 1
            else:
                ex.order = visual_order
                visual_order += 1
                last_superset_group = None

            normalized.append(ex)

        focus = _infer_focus(normalized)
        day_title = f"День {day_index}"
        if focus:
            day_title += f" — {focus}"

        days.append(
            ProgramDay(
                day_index=day_index,
                title=day_title,
                focus=focus,
                exercises=normalized,
            )
        )

    return TrainingProgram(
        title=title or "Импортированная программа тренировок",
        duration_type="week" if len(days) <= 7 else "program",
        days=days,
        source_type=source_type,
    )


def training_program_to_dict(program: TrainingProgram) -> dict[str, Any]:
    return asdict(program)


def format_program_import_preview(program: TrainingProgram) -> str:
    lines = [
        f"Я распознал программу: {program.title}",
        f"Тренировочных дней: {len(program.days)}",
        "",
    ]

    for day in program.days:
        lines.append(day.title)
        for ex in day.exercises:
            prefix = f"{ex.order}."
            if ex.superset_item:
                prefix = f"{ex.order}{ex.superset_item}."

            target_parts = []
            if ex.planned_reps:
                target_parts.append("-".join(str(x) for x in ex.planned_reps))
            elif ex.target_sets and ex.target_reps_min and ex.target_reps_max:
                if ex.target_reps_min == ex.target_reps_max:
                    target_parts.append(f"{ex.target_sets}×{ex.target_reps_min}")
                else:
                    target_parts.append(f"{ex.target_sets}×{ex.target_reps_min}-{ex.target_reps_max}")
            elif ex.target_sets and ex.target_reps_text:
                target_parts.append(f"{ex.target_sets}×{ex.target_reps_text}")
            elif ex.target_reps_text:
                target_parts.append(ex.target_reps_text)

            if ex.target_weight_kg is not None:
                target_parts.append(f"{ex.target_weight_kg:g} кг")

            line = f"{prefix} {ex.exercise_name}"
            if target_parts:
                line += " — " + ", ".join(target_parts)
            display_notes = []
            if ex.notes:
                for note in str(ex.notes).split(";"):
                    note = note.strip()
                    if not note:
                        continue
                    if note.startswith("planned_reps:"):
                        continue
                    if note.startswith("superset_group="):
                        continue
                    if note.startswith("superset_item="):
                        continue
                    display_notes.append(note)

            if display_notes:
                line += f" ({'; '.join(display_notes)})"

            lines.append(line)

        lines.append("")

    lines.append("Пока я ничего не добавляю в календарь.")
    lines.append("Следующий шаг — выбрать расклад по дням недели и подтвердить импорт.")

    return "\n".join(lines).strip()


def dump_training_program_json(program: TrainingProgram) -> str:
    return json.dumps(training_program_to_dict(program), ensure_ascii=False, indent=2)
