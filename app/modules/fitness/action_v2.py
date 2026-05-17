from __future__ import annotations
import json
import re
from datetime import date, datetime, timezone

from app.ai import claude_client
from app.db import (
    create_fitness_pending_decision,
    get_latest_fitness_pending_decision,
    resolve_fitness_pending_decision,
    get_today_planned_workout,
    get_planned_workouts_in_period,
    get_latest_planned_workout_template_by_focus,
    replace_planned_workout,
    save_training_plan,
    save_fitness_workout_session_v2,
    append_fitness_workout_sets_v2,
    append_exercise_to_existing_workout,
    update_fitness_pending_decision_context,
    delete_last_fitness_set_v2,
    update_last_fitness_set_v2,
    get_completed_workouts_in_period,
    get_personal_records,
    get_last_workout,
    get_last_measurement,
)
from app.modules.fitness.exercise_history import handle_exercise_history_request
from app.modules.fitness.router_hardening import handle_router_hardening
from app.modules.fitness.self_learning import (
    handle_self_correction,
    handle_forget_request,
    build_corrections_context,
    is_correction_message,
    is_forget_message,
)
from app.db import save_last_interaction
from app.modules.fitness.formatter import (
    format_planned_workout,
    format_period_plan,
    format_human_date,
    format_completed_period,
    format_personal_records,
    format_last_workout,
    format_last_measurement,
)
from app.modules.fitness.utils import (
    week_bounds,
    next_week_bounds,
    month_bounds,
    next_month_bounds,
)


def _safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


def _active_session_context_from_pending(pending: dict | None) -> dict | None:
    if not pending:
        return None
    if pending.get("decision_type") != "active_workout_session":
        return None
    return pending.get("context_json") or {}


def _normalize_exercises(raw_exercises: list[dict] | None) -> list[dict]:
    result = []
    for i, ex in enumerate(raw_exercises or [], start=1):
        name = (
            ex.get("exercise_name")
            or ex.get("name")
            or ex.get("title")
            or ex.get("exercise")
        )
        if not name:
            continue

        sets = ex.get("target_sets") or ex.get("sets")
        reps_text = ex.get("target_reps_text") or ex.get("reps_text")
        reps_min = ex.get("target_reps_min") or ex.get("reps_min")
        reps_max = ex.get("target_reps_max") or ex.get("reps_max")
        weight = ex.get("target_weight_kg") or ex.get("weight_kg")

        # Merge warmup_sets into notes for storage
        notes_parts = []
        if ex.get("notes"):
            notes_parts.append(str(ex["notes"]).strip())

        warmup = ex.get("warmup_sets") or []
        if warmup:
            warmup_str = "Разминка: " + ", ".join(
                f"{w.get('weight_kg')}×{w.get('reps_min', '')}-{w.get('reps_max', '')}"
                if w.get('reps_max') else f"{w.get('weight_kg')}×{w.get('reps_min', '')}"
                for w in warmup
            )
            notes_parts.append(warmup_str)

        if ex.get("superset_group"):
            notes_parts.append(f"Суперсет: {ex['superset_group']}")
        if ex.get("tempo"):
            notes_parts.append(f"Темп: {ex['tempo']}")
        if ex.get("rpe") is not None:
            notes_parts.append(f"RPE {ex['rpe']}")
        if ex.get("rest_seconds"):
            notes_parts.append(f"Отдых: {ex['rest_seconds']} с")

        notes = "\n".join(notes_parts) or None

        result.append({
            "exercise_order": ex.get("exercise_order") or i,
            "exercise_name": name,
            "target_sets": sets,
            "target_reps_min": reps_min,
            "target_reps_max": reps_max,
            "target_reps_text": reps_text,
            "target_weight_kg": weight,
            "notes": notes,
        })
    return result


def _normalize_logged_exercises(raw_exercises: list[dict] | None) -> list[dict]:
    result = []
    for ex in raw_exercises or []:
        name = (
            ex.get("exercise_name")
            or ex.get("name")
            or ex.get("title")
            or ex.get("exercise")
        )
        if not name:
            continue

        sets = []
        for i, s in enumerate(ex.get("sets") or [], start=1):
            reps_raw = s.get("reps")
            weight = s.get("weight_kg")
            notes = s.get("notes")
            tags = []
            if s.get("is_warmup"):
                tags.append("разминка")
            if s.get("is_drop"):
                tags.append("дроп")
            if s.get("is_failure"):
                tags.append("до отказа")
            duration = s.get("duration_seconds") or s.get("duration_sec")
            distance = s.get("distance_m") or s.get("distance_meters")
            if duration:
                tags.append(f"{duration} с")
            if distance:
                tags.append(f"{distance} м")

            # reps может прийти строкой (AMRAP, до отказа) — конвертим в int или сдвигаем в notes
            reps_int: int | None = None
            if isinstance(reps_raw, (int, float)):
                reps_int = int(reps_raw)
            elif isinstance(reps_raw, str):
                m = re.search(r"\d+", reps_raw)
                if m:
                    reps_int = int(m.group())
                else:
                    tags.append(reps_raw)

            if tags:
                notes = (notes + " | " if notes else "") + ", ".join(tags)

            if reps_int is None and weight is None and not notes and not duration:
                continue

            sets.append({
                "set_number": s.get("set_number") or i,
                "weight_kg": weight,
                "reps": reps_int,
                "rpe": s.get("rpe"),
                "notes": notes,
            })

        if sets:
            ex_notes = ex.get("notes")
            if ex.get("superset_group"):
                ex_notes = (ex_notes + " | " if ex_notes else "") + f"Суперсет: {ex['superset_group']}"
            result.append({
                "exercise_name": name,
                "sets": sets,
                "notes": ex_notes,
            })

    return result


_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "среду", "четверг", "пятница", "пятницу", "суббота", "субботу", "воскресенье"]


def _looks_like_monthly_plan(text: str) -> bool:
    """Detect a multi-week program: 2+ НЕДЕЛЯ headers."""
    return len(re.findall(r"НЕДЕЛЯ\s+\d+", text, re.IGNORECASE)) >= 2


def _split_weeks(text: str) -> list[tuple[str, str]]:
    """Split text into (week_label, week_text) chunks by НЕДЕЛЯ N header."""
    parts = re.split(r"(НЕДЕЛЯ\s+\d+[^\n]*)", text, flags=re.IGNORECASE)
    weeks = []
    i = 1
    while i < len(parts) - 1:
        label = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        weeks.append((label, label + "\n" + body))
        i += 2
    return weeks


def _looks_like_weekly_plan(text: str) -> bool:
    """Detect multi-day plan: several day-name headers with exercise content."""
    t = text.lower()
    days_found = sum(1 for d in _WEEKDAYS_RU if d in t)
    has_numbered = bool(re.search(r"^\s*\d+\.", text, re.MULTILINE))
    has_sets_pattern = bool(re.search(r"\d+\s*[×x✕]\s*\d+", text))
    has_kg = "кг" in t
    has_reps_marker = any(x in t for x in ["повторен", "подход"])
    has_exercises = has_numbered or has_sets_pattern or has_kg or has_reps_marker
    # 3+ weekdays with any exercise marker = weekly plan
    # 2 weekdays only if there's clear exercise structure
    if days_found >= 3 and has_exercises:
        return True
    if days_found >= 2 and has_numbered:
        return True
    return False


def _looks_like_complex_plan(text: str) -> bool:
    """Detect structured multi-exercise workout plan text."""
    t = text.lower()
    has_numbered = bool(re.search(r"^\s*\d+\.", text, re.MULTILINE))
    has_sets_pattern = bool(re.search(r"\d+\s*[×x×]\s*\d+", text))
    has_kg = "кг" in t
    has_warmup = any(x in t for x in ["разминка", "рабочие подходы", "разминочн"])
    has_many_exercises = len(re.findall(r"^\s*\d+\.", text, re.MULTILINE)) >= 3
    return (has_numbered and has_sets_pattern and has_kg) or (has_warmup and has_sets_pattern) or has_many_exercises


async def parse_complex_workout_plan(text: str, target_date: str | None = None) -> dict:
    """
    Parse a rich multi-exercise workout plan with warm-up sets, working sets,
    equipment notes, and rep ranges. Returns dict compatible with add_custom_workout.
    """
    today = target_date or date.today().isoformat()

    system_prompt = f"""Ты парсер структурированной программы тренировки. Сегодня: {today}.

Пользователь описал детальную тренировку. Распарси её ПОЛНОСТЬЮ в JSON.

═══ ОБЩИЕ ПРАВИЛА ═══
- Разминочные подходы (секция "Разминка:", "разминочные", "разогрев") → warmup_sets массив + notes
- Рабочие подходы (секция "Рабочие:", "Working sets", или без явной метки) → target_sets/reps/weight
- Десятичные: "17,5" → 17.5
- Диапазон повторений "10–12" / "10-12" / "10..12" / "от 10 до 12" → target_reps_min=10, target_reps_max=12
- Точные повторы "10" → target_reps_min=10, target_reps_max=10
- Диапазон весов "8–9 кг" / "8-9кг" → target_weight_kg=8.5, notes="Диапазон веса: 8–9 кг"
- Настройки тренажёра ("сидушка — 2 дырки", "наклон 30°", "узкий хват") → notes упражнения
- Цели ("Цель: 90×12", "сделать лучше чем"), условия ("если получится", "только если") → notes
- "AMRAP" / "до отказа" / "макс" → target_reps_text="AMRAP", target_reps_min=null
- RPE/RIR ("RPE 8", "RIR 2", "оставь 2 в запасе") → notes
- Темп "3-1-2-0" / "3010" → notes ("Темп: 3-1-2-0")
- % от 1ПМ ("80% 1ПМ", "@75%") → notes

═══ НОТАЦИИ ПОДХОДОВ (распознавай ВСЕ) ═══
- "90 кг × 10 × 4 подхода" / "90×10×4" / "4 подхода 90 на 10" / "4×10 с 90 кг" → 4 sets, 90 kg, 10 reps
- "4 на 10, 90 кг" / "4 по 10 90кг" / "4х10 90" → 4 sets, 90 kg, 10 reps
- "3 подхода по 12 повторений с 50 кг" → 3 sets, 50 kg, 12 reps
- Перечисление каждого подхода:
    "90×12 / 90×12 / 90×8 / 90×8" → 4 явных подхода
    "60-70-80-70-60" (пирамида) → target_sets=5, notes="Пирамида: 60-70-80-70-60"
    "65/70/70/65" → target_sets=4, target_weight_kg=65 (min), notes="Прогрессия по подходам: 65/70/70/65"
- "5×5" / "5*5" / "5x5" → target_sets=5, target_reps_min=5
- "3×AMRAP" → target_sets=3, target_reps_text="AMRAP"
- "до отказа × 3 подхода" → target_sets=3, target_reps_text="до отказа"
- "100×5, отдых 15с, ×3, отдых 15с, ×2" (rest-pause/cluster) → target_sets=3, notes="Rest-pause 5+3+2"
- "80→70→60×8" (drop set) → target_sets=3, notes="Drop set: 80→70→60×8"
- "21" (биц 7+7+7) → target_reps_text="21 (7+7+7)"

═══ СУПЕРСЕТЫ И КОМПЛЕКСЫ ═══
- "Суперсет: A) жим лёжа 4×10 + B) тяга 4×10" → у обоих exercises: superset_group="A"
- "СС1: ..., СС2: ..." → группы superset_group="СС1", "СС2"
- "1A. Жим / 1B. Тяга / 2A. Присед / 2B. Сгибание" → группы "1", "2"
- "Трисет: A) ... B) ... C) ..." → у всех трёх superset_group одинаковый, superset_size=3
- "Круговая: ..., 4 круга" → каждое упражнение superset_group="круг", target_sets=4
- "Гигантский сет" / "Giant set" → superset_group="гигант"
- "Мехдроп" / "механический дроп" → notes
- Если суперсета НЕТ — superset_group=null

═══ JSON-СХЕМА (СТРОГО) ═══
{{
  "action": "add_custom_workout",
  "confidence": 0.95,
  "date": "{today}",
  "workout": {{
    "title": "Грудь силовая + дельта + трицепс",
    "focus": "chest",
    "focus_label": "грудь + дельта + трицепс",
    "notes": null,
    "exercises": [
      {{
        "exercise_name": "Жим штанги лёжа",
        "target_sets": 4,
        "target_reps_min": 10,
        "target_reps_max": 12,
        "target_reps_text": null,
        "target_weight_kg": 90,
        "rpe": null,
        "superset_group": null,
        "tempo": null,
        "rest_seconds": null,
        "notes": "Цель: 90×12/12/8/8. Разминка: 20×8, 50×8, 70×6, 80×3",
        "warmup_sets": [
          {{"weight_kg": 20, "reps_min": 8, "reps_max": 10}},
          {{"weight_kg": 50, "reps_min": 8, "reps_max": 10}},
          {{"weight_kg": 70, "reps_min": 6, "reps_max": 8}},
          {{"weight_kg": 80, "reps_min": 3, "reps_max": 5}}
        ]
      }}
    ]
  }},
  "summary": "Тренировка грудь + дельта + трицепс на {today}"
}}

Только JSON. Без markdown. Распарси ВСЕ упражнения из текста. Не теряй ни одного."""

    response = await claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )
    return _safe_json_loads(response.content[0].text if response.content else "{}")


async def parse_weekly_plan(text: str) -> dict:
    """Parse a multi-day weekly/monthly plan into multiple planned_workouts."""
    today = date.today().isoformat()
    week_start, week_end = week_bounds()
    next_start, next_end = next_week_bounds()

    system_prompt = f"""Ты парсер недельной/многодневной программы тренировок. Сегодня: {today}.
Текущая неделя: {week_start} — {week_end}. Следующая: {next_start} — {next_end}.

Распарси КАЖДЫЙ день как отдельную тренировку. Распарси ВСЕ упражнения. Ничего не теряй.

═══ ДАТЫ И ДНИ ═══
- Заголовки: "Понедельник"/"Пн"/"ПН", "Вторник"/"Вт", "Среда"/"Ср", "Четверг"/"Чт", "Пятница"/"Пт", "Суббота"/"Сб", "Воскресенье"/"Вс"
- "День 1" / "Day 1" / "A" (в ABC/PPL/AB схемах) → если пользователь сказал "со следующего понедельника", раскладывай по порядку начиная с понедельника
- "Push / Pull / Legs" (PPL) → 3 тренировки, не привязаны к датам (planned_date=null, weekday=null), если не сказано иначе
- Если сказано "на следующую неделю" → planned_date в диапазоне {next_start} — {next_end}
- Если сказано "на этой неделе" → planned_date в диапазоне {week_start} — {week_end}
- "Отдых" / "rest day" / "восстановление" → создай день с title="Отдых", exercises=[]
- weekday: "monday"/"tuesday"/"wednesday"/"thursday"/"friday"/"saturday"/"sunday" или null

═══ ОБОЗНАЧЕНИЯ ПОДХОДОВ (распознавай все варианты) ═══
- "90 кг × 10–12 × 4 подхода" / "90×10–12×4" / "4 по 10-12 90кг" / "4 на 10-12 с 90" → 4 sets, 90 kg, 10–12 reps
- "4×10" / "4х10" / "4*10" / "4 на 10" / "4 по 10" → 4 sets × 10 reps
- "5×5 @ 80" / "5×5 80кг" → 5×5 с 80 кг
- "90×12 / 90×12 / 90×8 / 90×8" → target_sets=4, target_weight_kg=90, target_reps_min=8, target_reps_max=12
- "65/70/70/65 × 10–12" (4 подхода с разными весами) → target_sets=4, target_weight_kg=65, notes="Прогрессия: 65/70/70/65×10–12"
- "10/10/8/8" (одинаковый вес, разные повторы) → target_sets=4
- "60-70-80-70-60" (пирамида) → target_sets=5, notes="Пирамида: 60-70-80-70-60"
- "8–9 кг" (диапазон веса) → target_weight_kg=8.5, notes="Диапазон: 8–9 кг"
- "3 × 12–15" (без веса, своим весом) → target_sets=3, target_weight_kg=null
- "AMRAP", "до отказа", "до жжения", "макс" → target_reps_text="AMRAP" или "до отказа", target_reps_min=null
- "5+5+5" (rest-pause) → notes="Rest-pause 5+5+5", target_sets=1
- "80→70→60×8" (drop set) → notes="Drop set 80→70→60×8"
- "21s" (биц) → target_reps_text="21 (7+7+7)"
- "RPE 8" / "RIR 2" → rpe=8 (или сохраняй в notes если нет поля)
- Темп "3-1-2-0" / "3010" → tempo поле или notes
- "%" от 1ПМ ("@75%") → notes

═══ СУПЕРСЕТЫ И КОМПЛЕКСЫ ═══
- "Суперсет А) ... Б) ..." / "СС1: ... + ..." / "1A. ... 1B. ..." → у этих упражнений одинаковый superset_group
- "Трисет" → 3 упражнения в одной superset_group
- "Круговая: ..., 4 круга" → superset_group="круг1", target_sets=4
- Если не суперсет → superset_group=null

═══ ОСОБЫЕ КОНСТРУКЦИИ ═══
- "Разминка: 20×8, 50×8, 70×6, 80×3" → сохрани В notes упражнения как "Разминка: ..."
- "Рабочие:" / "рабочие подходы:" — это основные, используй для target_sets/weight/reps
- "Вариант А / Вариант Б" → Вариант А = основные target_*, Вариант Б → notes
- "Если X легко — переходи на Y", "только если колени спокойны", "если плечи живые" → notes
- "Цель: 90×12 / 90×12 / 90×8 / 90×8" → notes
- "Опционально" / "если останутся силы" → notes ("Опционально")
- Настройки тренажёра ("сидушка — 2 дырки", "наклон 30°", "узкий хват") → notes
- "Не до отказа", "техника важнее веса", "чисто, без читинга" → notes
- "Компенсация 50 кг" (гравитрон) → target_weight_kg=50, notes="Компенсация (гравитрон)"
- "7–10 минут" (кардио) → target_reps_text="7–10 минут", target_sets=1

═══ FOCUS КАЖДОГО ДНЯ ═══
chest / back / legs / shoulders / arms / full_body / push / pull / cardio / abs / rest

═══ JSON-СХЕМА (СТРОГО) ═══
{{
  "action": "create_weekly_plan",
  "confidence": 0.95,
  "plan": {{
    "plan_name": "Программа недели",
    "period_type": "week",
    "start_date": "{week_start}",
    "end_date": "{week_end}",
    "planned_workouts": [
      {{
        "planned_date": "YYYY-MM-DD",
        "weekday": "monday",
        "title": "Грудь + дельта + трицепс",
        "focus": "chest",
        "focus_label": "грудь + дельта + трицепс",
        "notes": null,
        "exercises": [
          {{
            "exercise_name": "Жим штанги лёжа",
            "target_sets": 4,
            "target_reps_min": 8,
            "target_reps_max": 12,
            "target_reps_text": null,
            "target_weight_kg": 90,
            "rpe": null,
            "superset_group": null,
            "tempo": null,
            "notes": "Цель: 90×12/12/8/8. Разминка: 20×8-10, 50×8-10, 70×6-8, 80×3-5"
          }}
        ]
      }}
    ]
  }},
  "summary": "5 тренировок на неделю"
}}

Только JSON. Без markdown. Распарси ВСЕ упражнения. Не пропускай дни."""

    response = await claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )
    return _safe_json_loads(response.content[0].text if response.content else "{}")


async def _save_weekly_plan(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    plan_data = parsed.get("plan") or {}
    planned_workouts_raw = plan_data.get("planned_workouts") or []

    if not planned_workouts_raw:
        return "Не смог распарсить расписание. Убедись что каждый день начинается с названия (Понедельник, Вторник и т.д.)."

    planned_workouts = _normalize_exercises_plan(planned_workouts_raw)

    plan_id = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name=plan_data.get("plan_name") or "Недельная программа",
        period_type=plan_data.get("period_type") or "week",
        start_date=plan_data.get("start_date"),
        end_date=plan_data.get("end_date"),
        source_text=text,
        notes=plan_data.get("notes"),
        planned_workouts=planned_workouts,
    )

    count = len(planned_workouts)
    days_summary = []
    for pw in planned_workouts:
        raw_date = pw.get("planned_date")
        if raw_date:
            date_str = format_human_date(raw_date[:10] if isinstance(raw_date, str) else raw_date)
        else:
            date_str = pw.get("weekday", "—")
        title = pw.get("title") or pw.get("focus_label") or "Тренировка"
        ex_count = len(pw.get("exercises") or [])
        days_summary.append(f"  • {date_str}: {title} ({ex_count} упр.)")

    lines = [
        f"✅ Записал программу на {count} тренировок. ID: {plan_id}",
        "",
    ] + days_summary

    return "\n".join(lines)


async def _save_monthly_plan(telegram_user_id: str | None, full_text: str) -> str:
    """Parse each week separately and save all plans."""
    weeks = _split_weeks(full_text)
    if not weeks:
        return "Не смог разбить программу по неделям. Убедись что каждая неделя начинается с 'НЕДЕЛЯ N'."

    # Extract footer notes (Правила, restrictions etc.) — text after last week
    last_week_end = full_text.rfind(weeks[-1][0])
    footer_notes = full_text[last_week_end + len(weeks[-1][1]):].strip() if last_week_end >= 0 else ""

    results = []
    total_workouts = 0
    errors = []

    for i, (week_label, week_text) in enumerate(weeks, 1):
        try:
            parsed = await parse_weekly_plan(week_text)
            plan_data = parsed.get("plan") or {}
            planned_workouts_raw = plan_data.get("planned_workouts") or []

            if not planned_workouts_raw:
                errors.append(f"Неделя {i}: не удалось распарсить")
                continue

            planned_workouts = _normalize_exercises_plan(planned_workouts_raw)
            count = len(planned_workouts)

            plan_notes = plan_data.get("notes") or ""
            if footer_notes and i == len(weeks):
                plan_notes = (plan_notes + "\n\n" + footer_notes[:500]).strip()

            plan_id = await save_training_plan(
                telegram_user_id=telegram_user_id,
                plan_name=week_label,
                period_type="week",
                start_date=plan_data.get("start_date"),
                end_date=plan_data.get("end_date"),
                source_text=week_text[:500],
                notes=plan_notes or None,
                planned_workouts=planned_workouts,
            )

            total_workouts += count
            days = []
            for pw in planned_workouts:
                title = pw.get("title") or pw.get("focus_label") or "Тренировка"
                ex_c = len(pw.get("exercises") or [])
                days.append(f"{title} ({ex_c} упр.)")
            results.append(f"  {week_label}: {', '.join(days)}")

        except Exception as e:
            errors.append(f"Неделя {i}: ошибка — {e}")

    lines = [f"✅ Программа на {len(weeks)} недели сохранена. Всего тренировок: {total_workouts}", ""]
    lines += results
    if errors:
        lines += ["", "⚠️ Проблемы:"] + errors
    if footer_notes:
        lines += ["", f"📋 Правила программы сохранены в заметках последней недели."]

    return "\n".join(lines)


async def parse_fitness_action_v2(
    text: str,
    active_session: dict | None = None,
    telegram_user_id: str | None = None,
) -> dict:
    today = date.today().isoformat()
    current_week_start, current_week_end = week_bounds()
    next_week_start, next_week_end = next_week_bounds()
    month_start, month_end = month_bounds()
    next_month_start, next_month_end = next_month_bounds()

    # ── Self-learning: inject active corrections from this user ──
    corrections_block = ""
    if telegram_user_id:
        try:
            corrections_block = await build_corrections_context(telegram_user_id)
        except Exception:
            corrections_block = ""

    system_prompt = f"""
Ты главный parser фитнес-ассистента. Возвращай СТРОГО JSON без markdown.

Контекст:
- Сегодня: {today}
- Текущая неделя: {current_week_start} — {current_week_end}
- Следующая неделя: {next_week_start} — {next_week_end}
- Текущий месяц: {month_start} — {month_end}
- Следующий месяц: {next_month_start} — {next_month_end}
- Активная сессия: {json.dumps(active_session or {}, ensure_ascii=False)}

{corrections_block}

═══ JSON-СХЕМА ═══
{{
  "action": "show_today_workout | show_yesterday_workout | show_tomorrow_workout | show_week_plan | show_next_week_plan | show_month_plan | show_next_month_plan | show_workout_on_date | show_last_workout | show_completed_day | show_completed_week | show_completed_month | show_completed_period | show_next_workout | compare_weeks | quick_stats | replace_today_workout | add_custom_workout | log_workout_sets | continue_current_exercise | finish_workout | correct_previous_action | delete_last_set | edit_last_set | move_workout | copy_workout | edit_plan | show_progress | show_exercise_stats | show_personal_records | add_note | record_measurement | import_program | export_workouts | dangerous_delete | help | show_learned_rules | non_fitness | unknown | clarify",
  "confidence": 0.0,
  "date": null,
  "weekday": null,
  "period": {{"start_date": null, "end_date": null, "period_type": null}},
  "target": {{
    "focus": null,
    "focus_label": null,
    "exercise_name": null,
    "set_number": null,
    "note_text": null,
    "from_date": null,
    "to_date": null
  }},
  "workout": {{
    "title": null,
    "focus": null,
    "focus_label": null,
    "notes": null,
    "exercises": [
      {{
        "exercise_name": null,
        "target_sets": null,
        "target_reps_min": null,
        "target_reps_max": null,
        "target_reps_text": null,
        "target_weight_kg": null,
        "rpe": null,
        "superset_group": null,
        "tempo": null,
        "notes": null
      }}
    ]
  }},
  "logged_exercises": [
    {{
      "exercise_name": null,
      "superset_group": null,
      "sets": [
        {{"set_number": null, "weight_kg": null, "reps": null, "rpe": null, "is_warmup": false, "is_drop": false, "is_failure": false, "notes": null}}
      ],
      "notes": null
    }}
  ],
  "measurement": {{"weight_kg": null, "waist_cm": null, "chest_cm": null, "hips_cm": null, "arm_cm": null, "thigh_cm": null, "neck_cm": null, "bodyfat_pct": null, "notes": null}},
  "correction": {{"field": null, "old_value": null, "new_value": null}},
  "needs_confirmation": false,
  "summary": ""
}}

═══ КОМАНДЫ И ИХ ВАРИАЦИИ ═══

1. ПРОСМОТР (show_*):
- "что сегодня", "тренировка на сегодня", "что у меня сегодня", "план на сегодня", "что делаем" → show_today_workout
- "что вчера было", "вчерашняя тренировка" → show_yesterday_workout
- "что завтра", "тренировка завтра", "завтрашняя" → show_tomorrow_workout
- "план на неделю", "что на этой неделе", "недельный план", "тренировки недели" → show_week_plan
- "следующая неделя", "что на след неделе", "план на следующую" → show_next_week_plan
- "план на месяц", "месячный план" → show_month_plan / show_next_month_plan
- "что в пятницу", "тренировка в пн", "что 20-го" → show_workout_on_date. date = вычисли ближайшую такую.
- "что я делал в прошлый раз", "последняя тренировка", "что было на прошлой тренировке" → show_last_workout
- "что я сделал сегодня", "что я делал сегодня", "что я зафиксировал сегодня", "тренировка которую я сегодня сделал" → show_completed_day (date=today)
- "что я делал вчера", "вчерашняя выполненная тренировка" → show_completed_day с date=вчера
- "что я делал 15-го", "что было 12 мая" → show_completed_day с явной date
- "что я сделал на этой неделе", "тренировки за неделю", "отчёт за неделю", "сводка за неделю" → show_completed_week
- "что я делал в прошлую неделю", "тренировки прошлой недели" → show_completed_period с period.start_date/end_date
- "что я сделал в этом месяце", "отчёт за месяц", "сводка за месяц", "тренировки месяца" → show_completed_month
- "за период с X по Y", "с понедельника по пятницу", "с 1 по 15 мая" → show_completed_period
- "что дальше", "следующая тренировка", "когда след треня", "что у меня дальше по плану" → show_next_workout
- "сравни эту неделю с прошлой", "сравни недели", "прогресс по неделям" → compare_weeks
- "сводка", "быстрая статистика", "что у меня", "статус", "кратко" (без указания периода) → quick_stats
- "как у меня жим", "прогресс по приседу", "история жима", "сколько я жал" → show_exercise_stats. target.exercise_name = "жим штанги".
- "мои рекорды", "PR", "ПР", "личный рекорд по жиму", "максимум" → show_personal_records
- "сколько раз я приседал в этом месяце", "статистика за месяц" → show_progress с period.

2. ЗАМЕНА сегодняшней (replace_today_workout):
- "сегодня вместо ног делаем плечи"
- "замени сегодняшнюю на плечи"
- "поставь на сегодня плечи"
- "сегодня тренировка: жим гантелей, разводка, фронтальный подъём" → replace_today_workout, заполни workout.exercises

3. ДОБАВЛЕНИЕ (add_custom_workout):
- "добавь тренировку на сегодня/завтра/пятницу", "поставь тренировку на ...", "запиши тренировку на завтра"

4. ЗАПИСЬ ФАКТА (log_workout_sets):
КЛЮЧЕВОЕ — распознавай ВСЕ варианты:
- "записываем сегодняшнюю тренировку..."
- "жим: 80×5, 80×5, 75×8" / "жим 80 на 5, 80 на 5, 75 на 8"
- "присед 4 подхода 100×5" / "присед 4×5 100кг" / "100 на 5 четыре раза"
- "сделал жим: 1) 80×5, 2) 80×5, 3) 75×6" (явная нумерация)
- "первый 80×5, второй 80×5, третий 75×6" / "первый подход 80 на 5..."
- "жим штанги 4 на 10 по 25 кг" → 4 sets × 10 reps × 25 kg, exercise="жим штанги"
- "3 подхода по 12 повторений с весом 50 кг"
- "до отказа на брусьях × 4 подхода" → reps=null, notes="до отказа"
- "AMRAP подтягивания × 3" → reps=null, notes="AMRAP"
- "брусья: ширина+узко+обратный — суперсет 4 круга" → 3 упражнения в одной superset_group
- Несколько упражнений в одном сообщении:
    "Запиши тренировку: жим 4×10 80кг, потом бицепс 25кг 4×15, потом брусья 4 до отказа, пресс v-складка 4×12, трицепс канат 25×14×3"
    → ПЯТЬ объектов в logged_exercises: жим, бицепс, брусья, пресс, трицепс. КАЖДЫЙ со своими подходами.
- Разделители упражнений: "потом", "затем", "далее", "после", "следующее", ".", ";", новая строка, или явное название нового упражнения
- Числительные словами: "первый/второй/третий", "раз/два/три", "восемь раз"
- "RPE 8" / "оставил 2 в запасе" → rpe в подходе
- "разминка 20×10, потом рабочий 80×5×5" → разминка = is_warmup:true, рабочие = is_warmup:false
- "дроп: 80→70→60×8" → 3 подхода с is_drop:true
- "rest-pause 80×5+3+2" → одно упражнение, 3 подхода, notes="Rest-pause"
- "до отказа" / "до жжения" / "макс" → is_failure:true
- Десятичные: "17,5" → 17.5
- Если есть активная сессия и: "третий 20 на 10", "следующий 20 на 8", "ещё 20 на 6" → continue_current_exercise
- "закончил тренировку", "всё, хватит", "финиш", "сохраняй" → finish_workout

5. ИСПРАВЛЕНИЯ (correct_previous_action / edit_last_set / delete_last_set):
- "не 20, а 17.5" / "во втором было 12, не 10" / "поменяй последний на 80×6"
- "поправь последний подход: 85×5", "исправь второй: 80×8"
→ edit_last_set если правится последний/конкретный подход в активной сессии.
  Поставь target.set_number (если указан), correction.field ("weight_kg" или "reps"), correction.new_value.
- "ой, не на сегодня — на пятницу", "перепутал, не жим а тяга" → correct_previous_action.
- "удали последний подход", "убери последний сет", "не последний", "удали" в активной сессии → delete_last_set

6. ПЕРЕМЕЩЕНИЕ ТРЕНИРОВКИ (move_workout / copy_workout):
- "перенеси пятницу на среду", "сегодняшнюю на завтра"
- "скопируй понедельник на четверг", "продублируй вторник"
→ move_workout / copy_workout. target.from_date, target.to_date.

7. РЕДАКТИРОВАНИЕ ПЛАНА (edit_plan):
- "добавь жим в сегодняшнюю", "вставь тягу в план на четверг"
- "убери присед из вторника", "удали жим из плана"
- "замени жим лёжа на жим гантелей", "поменяй штангу на гантели"
- "увеличь вес в жиме до 90", "поставь 4 подхода вместо 3"
- "измени план", "давай поправим"

8. ПРОГРЕСС:
- "покажи прогресс", "результаты", "как я расту"
- "сколько раз я в зале был в этом месяце" → show_progress с period.
- "мой жим лёжа за последние 5 тренировок" → show_exercise_stats.

9. ЗАМЕТКИ (add_note):
- "заметка к тренировке: ...", "комментарий: ..."
- "запомни: локти ближе к телу" (если в активной сессии — к текущему упражнению)
- "к пятничной тренировке припиши: принести резинки"

10. ЗАМЕРЫ ТЕЛА (record_measurement):
- "вес 80.5 кг", "сегодня 80,5", "взвесился 80.5"
- "талия 82, грудь 100", "замеры: вес 80, талия 82, бицепс 38"
- "% жира 15", "процент жира 18"
→ record_measurement, заполни measurement.

11. ИМПОРТ ПРОГРАММЫ (import_program):
- "вот программа: пн — грудь...", "загрузи план", "запиши программу на 4 недели"

12. ЭКСПОРТ (export_workouts):
- "выгрузи тренировки", "экспортируй", "дай мне данные", "сделай csv"

13. УДАЛЕНИЕ ОПАСНОЕ (dangerous_delete, needs_confirmation=true):
- "удали все тренировки", "очисти историю", "сотри всё", "обнули"

14. ПОМОЩЬ (help):
- "что ты умеешь", "помощь", "как пользоваться", "команды"

14b. ВЫУЧЕННЫЕ ПРАВИЛА (show_learned_rules):
- "покажи правила", "что ты выучил", "какие у меня правила",
- "список правил", "что ты запомнил"

15. НЕ-ФИТНЕС сообщения → non_fitness:
- "какая сегодня погода", "сколько времени", "найди ресторан", "напомни купить молоко", "что такое X"
- любые вопросы и просьбы НЕ про тренировки/упражнения/замеры/еду-калории/прогресс/планы
- если активная сессия отсутствует и текст не похож на фитнес → non_fitness
- если в активной сессии — пользователь может задать общий вопрос ("сколько ккал в банане?", "как правильно дышать?") — это всё равно non_fitness, мы ответим как AI и не сломаем сессию

16. Если непонятно → clarify (confidence 0.3–0.6) или unknown (confidence < 0.3).

═══ ОБЩИЕ ПРАВИЛА ═══
- Десятичные через запятую: "17,5" → 17.5. Всегда.
- "×" / "x" / "х" / "*" / "на" / "по" — все эквивалентны как разделитель в "вес × повторы".
- Поддерживаемая команда: confidence >= 0.75.
- Если упражнение упомянуто без названия группы — определи focus сам.
"""

    # Auto-pick model: длинные мультиупражненческие сообщения и записи тренировок
    # дают намного лучший результат на sonnet.
    text_len = len(text)
    use_sonnet = text_len > 200 or any(x in text.lower() for x in [
        "потом", "затем", "далее", "после", "суперсет", "сс1", "сс2", "трисет",
        "запиши тренировку", "записать тренировку", "сегодняшнюю тренировку",
        "разминка", "рабочий", "рабочие", "дроп", "rest-pause", "пирамид",
    ])
    model = "claude-sonnet-4-6" if use_sonnet else "claude-haiku-4-5"
    max_tokens = 3072 if use_sonnet else 1024

    response = await claude_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )

    return _safe_json_loads(response.content[0].text if response.content else "{}")


async def _parse_finish_confirmation_with_ai_async(text: str, active_session: dict | None) -> dict:
    system_prompt = (
        f"Ты parser ответа на вопрос: 'Ты закончил тренировку?'\n"
        f"Активная сессия: {json.dumps(active_session or {}, ensure_ascii=False)}\n"
        "Верни строго JSON: {\"action\": \"finish|continue|danger|unclear\", \"confidence\": 0.0, \"summary\": \"\"}\n"
        "finish — закончил/хватит/сохраняй. continue — продолжает. danger — удалить. unclear — непонятно."
    )
    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=128,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )
    return _safe_json_loads(response.content[0].text if response.content else "{}")


async def _get_plan_for_period(telegram_user_id: str | None, start_date: str, end_date: str, title: str) -> str:
    items = await get_planned_workouts_in_period(
        telegram_user_id=telegram_user_id,
        start_date=start_date,
        end_date=end_date,
        include_cancelled=False,
    )
    return format_period_plan(items, title=title)


async def _create_or_replace_today_workout(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    today = date.today().isoformat()

    workout = parsed.get("workout") or {}
    exercises = _normalize_exercises(workout.get("exercises"))

    replacement = {
        "title": workout.get("title") or workout.get("focus_label") or "Кастомная тренировка",
        "focus": workout.get("focus"),
        "focus_label": workout.get("focus_label"),
        "notes": workout.get("notes"),
        "exercises": exercises,
    }

    # If user only gave focus, try to copy exercises from existing template.
    if not replacement["exercises"] and replacement.get("focus"):
        template = await get_latest_planned_workout_template_by_focus(
            telegram_user_id=telegram_user_id,
            focus=replacement.get("focus"),
            exclude_workout_id=None,
        )
        if template:
            copied = []
            for i, ex in enumerate(template.get("exercises") or [], start=1):
                copied.append({
                    "exercise_order": i,
                    "exercise_name": ex.get("exercise_name"),
                    "target_sets": ex.get("target_sets"),
                    "target_reps_min": ex.get("target_reps_min"),
                    "target_reps_max": ex.get("target_reps_max"),
                    "target_reps_text": ex.get("target_reps_text"),
                    "target_weight_kg": ex.get("target_weight_kg"),
                    "notes": ex.get("notes"),
                })
            replacement["exercises"] = copied
            replacement["notes"] = (replacement.get("notes") or "") + "\nУпражнения скопированы из шаблона."

    current = await get_today_planned_workout(telegram_user_id, today)

    if current:
        old = current["workout"]
        replacement_id = await replace_planned_workout(
            target_workout_id=old["id"],
            replacement=replacement,
            source_text=text,
        )
        updated = await get_today_planned_workout(telegram_user_id, today)
        return (
            "Заменил сегодняшнюю тренировку.\n\n"
            f"Было: {old.get('title') or old.get('focus_label')}\n\n"
            "Стало:\n"
            + format_planned_workout(updated)
        )

    # No workout today: create ad-hoc plan/workout for today.
    plan_id = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name="Ad hoc workout",
        period_type="day",
        start_date=today,
        end_date=today,
        source_text=text,
        notes="Created by Fitness Core v2",
        planned_workouts=[{
            "planned_date": today,
            "weekday": None,
            "sequence_number": 1,
            "is_floating": False,
            "title": replacement.get("title"),
            "focus": replacement.get("focus"),
            "focus_label": replacement.get("focus_label"),
            "workout_type": "custom",
            "status": "planned",
            "notes": replacement.get("notes"),
            "exercises": replacement.get("exercises") or [],
        }],
    )

    updated = await get_today_planned_workout(telegram_user_id, today)
    return (
        "На сегодня не было активной плановой тренировки. Создал новую.\n"
        f"ID плана: {plan_id}\n\n"
        + format_planned_workout(updated)
    )


async def _add_custom_workout(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    target_date = parsed.get("date") or date.today().isoformat()
    workout = parsed.get("workout") or {}
    exercises = _normalize_exercises(workout.get("exercises"))

    plan_id = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name="Ad hoc workout",
        period_type="day",
        start_date=target_date,
        end_date=target_date,
        source_text=text,
        notes="Created by Fitness Core v2",
        planned_workouts=[{
            "planned_date": target_date,
            "weekday": parsed.get("weekday"),
            "sequence_number": 1,
            "is_floating": False,
            "title": workout.get("title") or workout.get("focus_label") or "Кастомная тренировка",
            "focus": workout.get("focus"),
            "focus_label": workout.get("focus_label"),
            "workout_type": "custom",
            "status": "planned",
            "notes": workout.get("notes"),
            "exercises": exercises,
        }],
    )

    ex_count = len(exercises)
    ex_names = ", ".join(e["exercise_name"] for e in exercises[:4])
    if ex_count > 4:
        ex_names += f" и ещё {ex_count - 4}"

    lines = [
        f"Записал тренировку на {format_human_date(target_date)}.",
        f"ID плана: {plan_id}",
        f"Упражнений: {ex_count} — {ex_names}",
    ]
    if workout.get("notes"):
        lines.append(f"📝 {workout.get('notes')}")

    return "\n".join(lines)


async def _log_workout_sets(telegram_user_id: str | None, text: str, parsed: dict, active_session: dict | None) -> str:
    today = date.today().isoformat()
    workout_date = parsed.get("date") or today
    workout = parsed.get("workout") or {}
    logged_exercises = _normalize_logged_exercises(parsed.get("logged_exercises"))

    if not logged_exercises:
        return "Я понял, что ты записываешь тренировку, но не смог уверенно выделить подходы."

    # ── SMART CONTINUATION ─────────────────────────────────────────────────
    # Если есть активная сессия на ту же дату — НЕ создаём новую тренировку,
    # а добавляем упражнения/подходы к существующей.
    explicit_new = any(x in text.lower() for x in [
        "новую тренировку", "новая тренировка", "начни заново",
        "начни новую", "сброс", "обнови сессию",
    ])
    if active_session and active_session.get("workout_id") and not explicit_new:
        existing_workout_date = str(active_session.get("workout_date") or today)[:10]
        if existing_workout_date == workout_date:
            workout_id = int(active_session["workout_id"])
            total_added = 0
            for ex in logged_exercises:
                added = await append_exercise_to_existing_workout(
                    workout_id=workout_id,
                    exercise_name=ex["exercise_name"],
                    sets=ex["sets"],
                    source_text=text,
                )
                total_added += added

            current_exercise = logged_exercises[-1]["exercise_name"]
            active_session["current_exercise"] = current_exercise
            active_session["last_activity_at"] = _now_iso()
            active_session["last_training_activity_at"] = _now_iso()
            active_session["last_action"] = "log_workout_sets_append"
            pending = await get_latest_fitness_pending_decision(telegram_user_id)
            if pending and pending.get("decision_type") == "active_workout_session":
                await update_fitness_pending_decision_context(pending["id"], active_session)

            lines = [
                f"Продолжаю тренировку (ID: {workout_id}). Добавил подходы:",
                "",
            ]
            for ex in logged_exercises:
                lines.append(f"{ex['exercise_name']}:")
                for s in ex["sets"]:
                    weight = s.get("weight_kg")
                    reps = s.get("reps")
                    sn = s.get("set_number")
                    if weight is not None and reps is not None:
                        lines.append(f"  {sn}) {weight} кг × {reps}")
                    elif reps is not None:
                        lines.append(f"  {sn}) {reps} повторений")
                lines.append("")
            lines.append(f"Всего добавлено: {total_added}")
            lines.append(f"Текущее упражнение: {current_exercise}")
            lines.append("Скажи «закончил тренировку», когда всё.")
            return "\n".join(lines).strip()

    # ── NEW WORKOUT ────────────────────────────────────────────────────────
    workout_id = await save_fitness_workout_session_v2(
        telegram_user_id=telegram_user_id,
        workout_date=workout_date,
        workout_type="actual",
        focus=workout.get("focus"),
        focus_label=workout.get("focus_label"),
        source_text=text,
        notes=workout.get("notes"),
        exercises=logged_exercises,
    )

    current_exercise = logged_exercises[-1]["exercise_name"]
    total_sets = sum(len(ex["sets"]) for ex in logged_exercises)

    session_context = {
        "workout_id": workout_id,
        "workout_date": workout_date,
        "current_exercise": current_exercise,
        "session_status": "active",
        "started_at": _now_iso(),
        "last_activity_at": _now_iso(),
        "last_training_activity_at": _now_iso(),
        "last_action": "log_workout_sets",
    }

    await create_fitness_pending_decision(
        telegram_user_id=telegram_user_id,
        decision_type="active_workout_session",
        context=session_context,
        source_text=text,
    )

    lines = [
        "Записал тренировочную сессию.",
        f"ID тренировки: {workout_id}",
        "",
    ]

    for ex in logged_exercises:
        lines.append(f"{ex['exercise_name']}:")
        for s in ex["sets"]:
            weight = s.get("weight_kg")
            reps = s.get("reps")
            set_number = s.get("set_number")
            if weight is not None and reps is not None:
                lines.append(f"{set_number}) {weight} кг × {reps}")
            elif reps is not None:
                lines.append(f"{set_number}) {reps} повторений")
        lines.append("")

    lines.append(f"Всего записано подходов: {total_sets}")
    lines.append(f"Текущее упражнение: {current_exercise}")
    lines.append("Скажи «закончил тренировку», когда всё, или продолжай диктовать.")

    return "\n".join(lines).strip()


async def _continue_current_exercise(telegram_user_id: str | None, text: str, parsed: dict, active_session: dict | None) -> str:
    if not active_session or not active_session.get("workout_id"):
        return "Я понял подход, но не вижу активной тренировки. Скажи сначала: “Записываем сегодняшнюю тренировку...”"

    logged_exercises = _normalize_logged_exercises(parsed.get("logged_exercises"))

    # Sometimes parser may put only sets without exercise name.
    if not logged_exercises:
        sets = []
        for i, s in enumerate(parsed.get("sets") or [], start=1):
            if s.get("weight_kg") is not None or s.get("reps") is not None:
                sets.append({
                    "set_number": s.get("set_number") or i,
                    "weight_kg": s.get("weight_kg"),
                    "reps": s.get("reps"),
                    "rpe": s.get("rpe"),
                    "notes": s.get("notes"),
                })
        if sets:
            logged_exercises = [{
                "exercise_name": active_session.get("current_exercise"),
                "sets": sets,
            }]

    if not logged_exercises:
        return "Я понял, что это продолжение тренировки, но не смог выделить вес и повторы."

    exercise_name = logged_exercises[0].get("exercise_name") or active_session.get("current_exercise")
    sets = logged_exercises[0].get("sets") or []

    inserted = await append_fitness_workout_sets_v2(
        workout_id=int(active_session["workout_id"]),
        exercise_name=exercise_name,
        sets=sets,
        source_text=text,
    )

    active_session["current_exercise"] = exercise_name
    active_session["session_status"] = "active"
    active_session["last_activity_at"] = _now_iso()
    active_session["last_training_activity_at"] = _now_iso()
    active_session["last_action"] = "continue_current_exercise"

    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if pending and pending.get("decision_type") == "active_workout_session":
        await update_fitness_pending_decision_context(pending["id"], active_session)

    lines = [f"Записал подходы: {exercise_name}"]
    for s in sets:
        if s.get("weight_kg") is not None and s.get("reps") is not None:
            lines.append(f"- {s.get('weight_kg')} кг × {s.get('reps')}")
        elif s.get("reps") is not None:
            lines.append(f"- {s.get('reps')} повторений")
    lines.append(f"Добавлено подходов: {inserted}")

    return "\n".join(lines)


async def _delete_last_set(telegram_user_id: str | None, active_session: dict | None) -> str:
    if not active_session or not active_session.get("workout_id"):
        return "Не вижу активной тренировки, из которой можно удалить последний подход."

    deleted = await delete_last_fitness_set_v2(int(active_session["workout_id"]))
    if not deleted:
        return "Не нашёл последний подход для удаления."

    return (
        "Удалил последний подход:\n"
        f"{deleted.get('exercise_name')} — {deleted.get('weight_kg')} кг × {deleted.get('reps')}"
    )


async def _correct_previous_action(telegram_user_id: str | None, text: str, parsed: dict, active_session: dict | None) -> str:
    correction = parsed.get("correction") or {}
    field = correction.get("field")
    new_value = correction.get("new_value")

    if field in ("weight", "weight_kg", "reps") and active_session and active_session.get("workout_id"):
        updated = await update_last_fitness_set_v2(
            workout_id=int(active_session["workout_id"]),
            field="weight_kg" if field in ("weight", "weight_kg") else "reps",
            new_value=new_value,
        )
        if updated:
            return (
                "Исправил последний подход:\n"
                f"{updated.get('exercise_name')} — {updated.get('weight_kg')} кг × {updated.get('reps')}"
            )

    if field == "date":
        return (
            "Понял уточнение по дате. Сейчас я ещё не умею безопасно переносить уже созданное действие по такой короткой поправке. "
            "Скажи полной фразой, например: “поставь эту тренировку на сегодня”."
        )

    return "Понял, что это исправление, но не смог безопасно применить его автоматически."




def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_status(active_session: dict | None) -> str:
    if not active_session:
        return ""
    return active_session.get("session_status") or "active"


def _looks_like_set_message(text: str) -> bool:
    t = (text or "").lower().replace(",", ".")
    has_set_hint = any(x in t for x in [
        "перв", "втор", "трет", "четвер", "пят", "шест", "седьм",
        "следующ", "еще", "ещё", "последн"
    ])
    has_weight_reps = re.search(
        r"\d+(?:\.\d+)?\s*(?:кг|килограмм(?:ов|а)?|)?\s*(?:на|x|х|×)\s*\d+",
        t,
    )
    return bool(has_set_hint and has_weight_reps)


def _extract_set_number(text: str):
    t = (text or "").lower()
    mapping = [
        ("перв", 1),
        ("втор", 2),
        ("трет", 3),
        ("четвер", 4),
        ("пят", 5),
        ("шест", 6),
        ("седьм", 7),
        ("восьм", 8),
        ("девят", 9),
        ("десят", 10),
    ]
    for stem, value in mapping:
        if stem in t:
            return value
    return None




def _is_explicit_workout_recording_command(text: str) -> bool:
    """
    Long explicit workout-recording commands must go to the full AI parser,
    not to the short active-session regex parser.

    Example:
    “Записываем сегодняшнюю тренировку. Жим гантелей сидя.
     Первый подход 17.5 на 20, второй 20 на 12.”
    """
    t = (text or "").strip().lower().replace("ё", "е")

    explicit_start = any(x in t for x in [
        "записываем",
        "записываю",
        "записать тренировку",
        "запиши тренировку",
        "сегодняшнюю тренировку",
        "сегодняшняя тренировка",
        "начинаем тренировку",
        "начал тренировку",
    ])

    has_multiple_sets = (
        ("перв" in t and "втор" in t)
        or t.count(" на ") >= 2
        or t.count("×") >= 2
        or t.count(" x ") >= 2
        or t.count(" х ") >= 2
    )

    return explicit_start or (has_multiple_sets and len(t) > 45)

def _parse_set_from_short_text(text: str, active_session: dict | None) -> dict | None:
    if not active_session or not active_session.get("current_exercise"):
        return None

    t = (text or "").lower().replace(",", ".")
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:кг|килограмм(?:ов|а)?|)?\s*(?:на|x|х|×)\s*(\d+)",
        t,
    )
    if not m:
        return None

    return {
        "action": "continue_current_exercise",
        "confidence": 0.99,
        "logged_exercises": [
            {
                "exercise_name": active_session.get("current_exercise"),
                "sets": [
                    {
                        "set_number": _extract_set_number(text),
                        "weight_kg": float(m.group(1)),
                        "reps": int(m.group(2)),
                    }
                ],
            }
        ],
    }


def _fast_finish_response(text: str) -> str | None:
    t = (text or "").strip().lower().replace("ё", "е")

    danger_terms = [
        "удали",
        "сотри",
        "не сохраняй",
        "отмени тренировку",
        "удали тренировку",
        "очисти",
    ]

    continue_exact = {
        "нет",
        "не закончил",
        "не закончена",
        "продолжаю",
        "еще",
        "ещё",
        "еще делаю",
        "ещё делаю",
        "еще тренируюсь",
        "ещё тренируюсь",
        "продолжим",
        "пока нет",
        "не закрывай",
        "оставь открытой",
        "сейчас продолжу",
        "дай еще время",
        "дай ещё время",
    }

    continue_contains = [
        "не закончил",
        "не закрывай",
        "еще делаю",
        "ещё делаю",
        "еще тренируюсь",
        "ещё тренируюсь",
        "сейчас продолжу",
        "продолжаю",
        "дай еще",
        "дай ещё",
    ]

    finish_exact = {
        "да",
        "да закончил",
        "да, закончил",
        "закончил",
        "закончена",
        "все",
        "всё",
        "готово",
        "закрывай",
        "закрой",
        "сохрани",
        "сохраняй",
        "на сегодня все",
        "на сегодня всё",
        "тренировка закончена",
        "тренировку закончил",
        "хватит",
    }

    finish_contains = [
        "закончил тренировку",
        "тренировку закончил",
        "тренировка закончена",
        "закрывай тренировку",
        "закрой тренировку",
        "сохрани тренировку",
        "на сегодня все",
        "на сегодня всё",
        "на сегодня хватит",
    ]

    if any(x in t for x in danger_terms):
        return "danger"

    if t in continue_exact or any(x in t for x in continue_contains):
        return "continue"

    if t in finish_exact or any(x in t for x in finish_contains):
        return "finish"

    return None


def _parse_finish_confirmation_with_ai(text: str, active_session: dict | None) -> dict:
    """Kept for sync compatibility — prefer the async version."""
    return {"action": "unclear", "confidence": 0.0, "summary": ""}


async def _close_active_workout_session(telegram_user_id: str | None, active_session: dict, reason: str = "finished") -> str:
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if pending and pending.get("decision_type") == "active_workout_session":
        await resolve_fitness_pending_decision(pending["id"], status="resolved")

    return "Ок, тренировку закрыл. Записанные подходы сохранил."


async def _continue_active_workout_session(telegram_user_id: str | None, active_session: dict) -> str:
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if pending and pending.get("decision_type") == "active_workout_session":
        active_session["session_status"] = "active"
        active_session["last_activity_at"] = _now_iso()
        await update_fitness_pending_decision_context(pending["id"], active_session)

    current = active_session.get("current_exercise") or "текущее упражнение"
    return f"Ок, продолжаем. Текущее упражнение: {current}."


async def try_handle_active_workout_message(telegram_user_id: str | None, text: str) -> str | None:
    """
    Called before generic router.

    Handles context-only active workout messages:
    - “Третий 20 на 10”
    - “закончил тренировку”
    - replies to “Ты закончил тренировку?”

    But it must NOT swallow explicit new commands like:
    - “Записываем сегодняшнюю тренировку. Первый подход..., второй...”
    - “Запиши план на следующую неделю...”
    """

    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    if not pending or pending.get("decision_type") != "active_workout_session":
        return None

    active_session = pending.get("context_json") or {}
    status = _session_status(active_session)

    normalized = (text or "").strip().lower().replace("ё", "е")

    # Do not swallow explicit new planning commands.
    if "план" in normalized and any(w in normalized for w in ["запиши", "создай", "поставь", "составь"]):
        return None

    # Do not swallow full workout-recording commands.
    # They must go through the full Fitness Core v2 parser so multiple sets are preserved.
    if _is_explicit_workout_recording_command(text):
        return None

    # Finish/continue/danger commands should work even when session is just active,
    # not only when status == awaiting_finish_confirmation.
    fast = _fast_finish_response(text)

    if fast == "finish":
        return await _close_active_workout_session(telegram_user_id, active_session)

    if fast == "continue":
        return await _continue_active_workout_session(telegram_user_id, active_session)

    if fast == "danger":
        return (
            "Это опасное действие. Я не удаляю тренировку автоматически.\n"
            "Напиши явно: “да, удали текущую тренировку” или “нет, оставь”."
        )

    # If user sends a short set message, continue workout regardless of status.
    if _looks_like_set_message(text):
        parsed = _parse_set_from_short_text(text, active_session)
        if parsed:
            if status == "awaiting_finish_confirmation":
                active_session["session_status"] = "active"
                active_session["last_activity_at"] = _now_iso()
                await update_fitness_pending_decision_context(pending["id"], active_session)
            return await _continue_current_exercise(telegram_user_id, text, parsed, active_session)

    # If bot is waiting for finish confirmation, use AI parser as fallback.
    if status == "awaiting_finish_confirmation":
        parsed = await _parse_finish_confirmation_with_ai_async(text, active_session)
        action = parsed.get("action")
        confidence = float(parsed.get("confidence") or 0)

        if confidence >= 0.65:
            if action == "finish":
                return await _close_active_workout_session(telegram_user_id, active_session)
            if action == "continue":
                return await _continue_active_workout_session(telegram_user_id, active_session)
            if action == "danger":
                return (
                    "Это опасное действие. Я не удаляю тренировку автоматически.\n"
                    "Напиши явно: “да, удали текущую тренировку” или “нет, оставь”."
                )

        return (
            "Я не понял, закончил ты тренировку или продолжаешь.\n"
            "Можешь ответить обычным текстом: “закончил” или “продолжаю”."
        )

    return None


async def _edit_plan(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    from app.db import (
        get_best_planned_workout_for_edit,
        add_exercise_to_planned_workout,
        remove_exercise_from_planned_workout,
        replace_exercise_in_planned_workout,
    )
    target = parsed.get("target") or {}
    exercise_name = target.get("exercise_name")
    target_date = parsed.get("date")

    workout = await get_best_planned_workout_for_edit(telegram_user_id, target_date=target_date)
    if not workout:
        return "Не нашёл активный план для редактирования. Сначала создай план."

    workout_id = workout.get("id")
    summary = parsed.get("summary") or ""

    # Ask Claude to figure out exact edit intent
    edit_prompt = f"""
Пользователь хочет изменить план тренировки (ID {workout_id}).

Запрос: {text}
Текущий план: {json.dumps(workout, ensure_ascii=False, default=str)}

Верни JSON:
{{
  "operation": "add | remove | replace | unknown",
  "exercise_name": "название упражнения",
  "new_exercise_name": "новое название (только для replace)",
  "sets": null,
  "reps_min": null,
  "reps_max": null,
  "weight_kg": null
}}
Только JSON.
"""
    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=256,
        system="Ты parser команд редактирования фитнес-плана. Ответ только JSON.",
        messages=[{"role": "user", "content": edit_prompt}],
    )
    edit_parsed = _safe_json_loads(response.content[0].text if response.content else "{}")

    operation = edit_parsed.get("operation")
    ex_name = edit_parsed.get("exercise_name") or exercise_name

    if operation == "add" and ex_name:
        result = await add_exercise_to_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=workout_id,
            exercise_name=ex_name,
            target_sets=edit_parsed.get("sets") or 3,
            target_reps_min=edit_parsed.get("reps_min"),
            target_reps_max=edit_parsed.get("reps_max"),
            target_weight_kg=edit_parsed.get("weight_kg"),
            source_text=text,
        )
        if result.get("ok"):
            return f"Добавил {ex_name} в план."
        return result.get("message") or f"Не удалось добавить {ex_name}."

    if operation == "remove" and ex_name:
        result = await remove_exercise_from_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=workout_id,
            exercise_name=ex_name,
            source_text=text,
        )
        if result.get("ok"):
            return f"Убрал {ex_name} из плана."
        return result.get("message") or f"Упражнение {ex_name!r} не найдено в плане."

    if operation == "replace" and ex_name:
        new_name = edit_parsed.get("new_exercise_name")
        if not new_name:
            return "Не понял, на что заменить. Уточни: 'замени X на Y'."
        result = await replace_exercise_in_planned_workout(
            telegram_user_id=telegram_user_id,
            target_date=target_date or date.today().isoformat(),
            old_exercise_name=ex_name,
            new_exercise_name=new_name,
            source_text=text,
        )
        if result.get("ok"):
            return f"Заменил {ex_name} на {new_name}."
        return result.get("message") or f"Упражнение {ex_name!r} не найдено."

    return f"Уточни что изменить: добавить, убрать или заменить упражнение?\n\nТекущий план: {workout.get('title', 'без названия')}"


async def _show_progress(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    from app.db import get_last_workout, get_last_measurement, get_recent_exercise_history

    target = parsed.get("target") or {}
    exercise_name = target.get("exercise_name")

    lines = []

    if exercise_name:
        history = await get_recent_exercise_history(telegram_user_id, exercise_key=exercise_name, limit_workouts=5)
        if not history:
            return f"Нет истории по упражнению «{exercise_name}»."
        lines.append(f"История: {exercise_name}")
        for entry in history[:5]:
            date_str = str(entry.get("workout_date", ""))[:10]
            sets_info = ", ".join(
                f"{s.get('weight_kg')} кг × {s.get('reps')}"
                for s in (entry.get("sets") or [])
            )
            lines.append(f"  {date_str}: {sets_info}")
    else:
        last = await get_last_workout(telegram_user_id)
        if last:
            lines.append(f"Последняя тренировка: {str(last.get('workout_date', ''))[:10]}")
            for ex in (last.get("exercises") or [])[:5]:
                sets_info = ", ".join(
                    f"{s.get('weight_kg')} кг × {s.get('reps')}"
                    for s in (ex.get("sets") or [])
                )
                lines.append(f"  {ex.get('exercise_name')}: {sets_info}")

        measurement = await get_last_measurement(telegram_user_id)
        if measurement:
            lines.append(
                f"\nПоследние замеры ({str(measurement.get('measured_at', ''))[:10]}): "
                f"{measurement.get('weight_kg')} кг"
            )

    if not lines:
        return "Данных пока нет. Начни записывать тренировки!"

    return "\n".join(lines)


async def _add_note(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    from app.db import get_best_planned_workout_for_edit, get_last_workout
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    target = parsed.get("target") or {}
    note_text = target.get("note_text") or text.strip()
    exercise_name = target.get("exercise_name")
    target_date = parsed.get("date")

    # Try to add to planned workout first
    planned = await get_best_planned_workout_for_edit(telegram_user_id, target_date=target_date)
    if planned and planned.get("ok") if isinstance(planned, dict) else planned:
        workout_id = planned.get("id") or planned.get("planned_workout_id")
        if workout_id:
            async with get_session() as session:
                if exercise_name:
                    # Note on specific exercise in plan
                    await session.execute(sql_text("""
                        UPDATE planned_exercises
                        SET notes = :notes
                        WHERE planned_workout_id = :workout_id
                          AND lower(exercise_name) LIKE lower(:exercise_name)
                    """), {"notes": note_text, "workout_id": workout_id,
                           "exercise_name": f"%{exercise_name}%"})
                    return f"Добавил заметку к упражнению «{exercise_name}»:\n📝 {note_text}"
                else:
                    # Note on whole planned workout
                    await session.execute(sql_text("""
                        UPDATE planned_workouts SET notes = :notes WHERE id = :workout_id
                    """), {"notes": note_text, "workout_id": workout_id})
                    return f"Добавил заметку к тренировке:\n📝 {note_text}"

    # Fall back to last recorded workout
    last = await get_last_workout(telegram_user_id)
    if last:
        async with get_session() as session:
            await session.execute(sql_text("""
                UPDATE fitness_workouts SET notes = :notes WHERE id = :workout_id
            """), {"notes": note_text, "workout_id": last["id"]})
            return f"Добавил заметку к последней тренировке ({str(last.get('workout_date', ''))[:10]}):\n📝 {note_text}"

    return "Не нашёл тренировку для заметки. Укажи конкретную дату."


async def _import_program(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    from app.db import save_training_plan
    from app.modules.fitness.formatter import format_period_plan

    plan_data = parsed.get("plan") or {}
    planned_workouts = plan_data.get("planned_workouts") or []

    if not planned_workouts:
        return (
            "Не смог разобрать программу тренировок из текста.\n\n"
            "Отправь в формате:\n"
            "Понедельник — грудь: жим 4×8, разводка 3×12\n"
            "Среда — спина: тяга 4×8, подтягивания 3×10\n"
            "Пятница — ноги: приседания 4×8, жим ногами 3×12"
        )

    workouts_normalized = _normalize_exercises_plan(planned_workouts)
    result = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name=plan_data.get("plan_name") or "Импортированная программа",
        period_type=plan_data.get("period_type") or "custom",
        start_date=plan_data.get("start_date"),
        end_date=plan_data.get("end_date"),
        source_text=text,
        notes=plan_data.get("notes"),
        planned_workouts=workouts_normalized,
    )

    count = result.get("created_count", len(planned_workouts))
    return f"Программа импортирована. Создано {count} тренировок."


def _normalize_exercises_plan(planned_workouts: list[dict]) -> list[dict]:
    result = []
    for pw in planned_workouts:
        exercises = _normalize_exercises(pw.get("exercises"))
        result.append({**pw, "exercises": exercises})
    return result


async def _export_workouts(telegram_user_id: str | None) -> str:
    from app.db import get_last_workout
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    async with get_session() as session:
        result = await session.execute(sql_text("""
            SELECT
                w.workout_date,
                w.focus_label,
                w.notes AS workout_notes,
                s.exercise_name,
                s.set_number,
                s.weight_kg,
                s.reps,
                s.rpe,
                s.notes AS set_notes
            FROM fitness_workouts w
            JOIN fitness_exercise_sets s ON s.workout_id = w.id
            WHERE w.telegram_user_id = :uid
            ORDER BY w.workout_date DESC, w.id DESC, s.set_number ASC
            LIMIT 500
        """), {"uid": telegram_user_id})
        rows = result.fetchall()

    if not rows:
        return "Нет записанных тренировок для экспорта."

    lines = ["Дата | Фокус | Упражнение | Подход | Вес | Повторы | RPE | Заметка"]
    lines.append("—" * 60)
    for r in rows:
        weight = f"{r.weight_kg} кг" if r.weight_kg is not None else "—"
        rpe = str(r.rpe) if r.rpe else "—"
        notes = r.set_notes or ""
        lines.append(
            f"{str(r.workout_date)[:10]} | {r.focus_label or '—'} | {r.exercise_name} | "
            f"{r.set_number} | {weight} | {r.reps} | {rpe} | {notes}"
        )

    # Telegram message limit ~4096 chars
    output = "\n".join(lines)
    if len(output) > 3800:
        output = output[:3800] + f"\n\n... (показаны первые {len(rows)} записей)"

    return f"```\n{output}\n```"


async def _ask_clarification(text: str) -> str:
    """Generate a helpful clarification question instead of going silent."""
    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=256,
        system=(
            "Ты фитнес-ассистент. Пользователь написал что-то, но ты не уверен что именно нужно сделать. "
            "Напиши ОДНО короткое уточняющее сообщение (1-3 предложения) на русском. "
            "Скажи что ты понял и что именно непонятно. Предложи конкретные варианты что сделать. "
            "Не используй markdown. Будь конкретным и дружелюбным."
        ),
        messages=[{"role": "user", "content": f"Пользователь написал: {text}"}],
    )
    return response.content[0].text if response.content else "Не совсем понял — уточни что сделать?"


async def handle_fitness_action_v2(telegram_user_id: str | None, text: str) -> str | None:
    """Public entry. Wraps the inner handler with self-learning hooks."""

    # ── Self-learning gate: feedback / forget messages bypass normal flow ──
    forget_reply = await handle_forget_request(telegram_user_id, text)
    if forget_reply:
        return forget_reply

    correction_reply = await handle_self_correction(telegram_user_id, text)
    if correction_reply:
        return correction_reply

    # ── Normal flow ──
    response = await _handle_fitness_action_v2_inner(telegram_user_id, text)

    # Save last interaction for future self-corrections (fire & forget)
    if response and telegram_user_id:
        try:
            await save_last_interaction(
                telegram_user_id=telegram_user_id,
                input_text=text,
                bot_response=response,
                action=None,
                parsed=None,
            )
        except Exception:
            pass

    return response


async def _handle_fitness_action_v2_inner(telegram_user_id: str | None, text: str) -> str | None:
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    active_session = _active_session_context_from_pending(pending)

    # ── Plan import detection runs FIRST and overrides any stale pending/session ──
    # A long plan-shaped message is never a set log, regardless of pending state.
    is_long = len(text) > 800
    is_monthly = _looks_like_monthly_plan(text)
    is_weekly = _looks_like_weekly_plan(text)
    is_complex = _looks_like_complex_plan(text)
    has_plan_intent = any(x in text.lower() for x in [
        "запланируй", "запланир", "план на неделю", "план на следующ",
        "следующая неделя тренировок", "тренировки на неделю",
        "программа тренировок", "программу тренировок",
    ])

    # If text looks like a plan or is clearly a long program dump,
    # bypass the active_session check entirely.
    if is_monthly or is_weekly or is_complex or (is_long and has_plan_intent):
        # Clear any stale pending decision so the next short message doesn't
        # get misrouted as continuation of a previous session.
        if pending and pending.get("id"):
            try:
                await resolve_fitness_pending_decision(pending["id"], status="cancelled")
            except Exception:
                pass

        if is_monthly:
            return await _save_monthly_plan(telegram_user_id, text)

        if is_weekly:
            weekly_parsed = await parse_weekly_plan(text)
            if weekly_parsed.get("action") == "create_weekly_plan" and \
                    weekly_parsed.get("plan", {}).get("planned_workouts"):
                return await _save_weekly_plan(telegram_user_id, text, weekly_parsed)
            # Fall through to free-form save if parser couldn't structure it
            if is_long:
                from app.db import save_training_plan
                await save_training_plan(
                    telegram_user_id=telegram_user_id,
                    plan_name="Импортированная программа",
                    period_type="custom",
                    start_date=None,
                    end_date=None,
                    source_text=text,
                    notes=None,
                    planned_workouts=[],
                )
                return "Сохранил программу как свободный план (не смог разложить по дням автоматически). Если нужно — пришли её разбитой по дням, и я создам отдельные тренировки."

        if is_complex:
            complex_parsed = await parse_complex_workout_plan(text)
            if complex_parsed.get("action") in ("add_custom_workout", "replace_today_workout") and \
                    complex_parsed.get("workout", {}).get("exercises"):
                return await _add_custom_workout(telegram_user_id, text, complex_parsed)

        # Long plan-intent text but no structured match → save as free-form plan
        if is_long and has_plan_intent:
            from app.db import save_training_plan
            await save_training_plan(
                telegram_user_id=telegram_user_id,
                plan_name="Импортированная программа",
                period_type="custom",
                start_date=None,
                end_date=None,
                source_text=text,
                notes=None,
                planned_workouts=[],
            )
            return "Сохранил программу тренировок как свободный текст. Если хочешь, чтобы я разложил её по дням — пришли в более чётком формате."

    # ── Multi-exercise workout description bypasses single-exercise router ────
    t_lower = text.lower().replace("ё", "е")
    record_intent = any(x in t_lower for x in [
        "запиши тренировку", "запиши тренеровку", "записать тренировку",
        "запиши пожалуйста тренировку", "записать сегодняшнюю тренировку",
        "запиши тренировку сегодняшнюю", "сегодняшнюю тренировку запиши",
        "записываем тренировку", "записываем сегодняшнюю",
        "вот моя тренировка", "вот тренировка", "сегодня сделал",
        "сегодня делал", "сегодня было", "тренировка сегодня",
        "залогируй тренировку", "лог тренировки", "сохрани тренировку",
        "запиши треню", "записать треню", "сегодня в зале",
    ])
    transition_count = sum(
        t_lower.count(x)
        for x in [
            " потом ", " потом,", " затем ", " затем,",
            " далее ", " после ", " следом ", " следующее ",
            " дальше ", " еще ", " ещё ",
        ]
    )
    exercise_keywords = [
        "жим", "тяга", "бицепс", "трицепс", "присед", "становая",
        "брус", "подтяг", "пресс", "махи", "разводк", "разгибан",
        "сгибан", "выпад", "отжим", "икр", "ягодиц", "пуловер",
        "плечи", "грудь", "спина", "ноги", "дельта", "гантел",
        "штанг", "канат", "блок", "тренаж", "кросс", "румынск",
        "скотт", "молоток", "обратн", "гиперэкстенз", "планка",
        "v-склад", "скручиван",
    ]
    distinct_exercises = sum(1 for kw in exercise_keywords if kw in t_lower)
    has_sets_pattern = bool(re.search(r"\d+\s*[×x✕х]\s*\d+", text))
    has_count_phrases = bool(re.search(r"\d+\s*(подход|раз|повторен|круг)", t_lower))

    # Очень явный многоупражненческий лог:
    # - "запиши тренировку" + переходы или 3+ упражнения
    # - 3+ упражнения + 2+ перехода (даже без "запиши")
    # - 4+ упражнения вообще
    is_multi_exercise_record = (
        (record_intent and (transition_count >= 1 or distinct_exercises >= 3))
        or (distinct_exercises >= 3 and transition_count >= 2 and (has_sets_pattern or has_count_phrases))
        or (distinct_exercises >= 4 and (has_sets_pattern or has_count_phrases))
    )

    if is_multi_exercise_record and not active_session:
        parsed_multi = await parse_fitness_action_v2(text, active_session=active_session, telegram_user_id=telegram_user_id)
        if parsed_multi.get("action") == "log_workout_sets" and parsed_multi.get("logged_exercises"):
            return await _log_workout_sets(telegram_user_id, text, parsed_multi, active_session)

    # ── Router hardening (handles set logging, confirmation flows, etc.) ───────
    hardening_reply = await handle_router_hardening(telegram_user_id, text)
    if hardening_reply is not None:
        return hardening_reply

    history_reply = await handle_exercise_history_request(
        telegram_user_id=telegram_user_id,
        text=text,
        active_session=active_session,
    )
    if history_reply is not None:
        return history_reply

    parsed = await parse_fitness_action_v2(text, active_session=active_session, telegram_user_id=telegram_user_id)

    action = parsed.get("action")
    confidence = float(parsed.get("confidence") or 0)

    if not action or action in ("unknown", "clarify") or confidence < 0.55:
        # Если текст явно НЕ про фитнес и нет активной сессии — отвечаем как общий AI
        if not active_session and not _looks_like_fitness_text(text):
            from app.ai import generate_general_answer
            return await generate_general_answer(text)
        return await _ask_clarification(text)

    if action == "show_today_workout":
        data = await get_today_planned_workout(telegram_user_id, date.today().isoformat())
        if not data:
            return "На сегодня активная плановая тренировка не найдена."
        return "Сегодня по плану:\n\n" + format_planned_workout(data)

    if action == "show_week_plan":
        start, end = week_bounds()
        return await _get_plan_for_period(telegram_user_id, start, end, f"План недели ({start} — {end})")

    if action == "show_next_week_plan":
        start, end = next_week_bounds()
        return await _get_plan_for_period(telegram_user_id, start, end, f"План на следующую неделю ({start} — {end})")

    if action == "show_month_plan":
        start, end = month_bounds()
        return await _get_plan_for_period(telegram_user_id, start, end, f"План на текущий месяц ({start} — {end})")

    if action == "show_next_month_plan":
        start, end = next_month_bounds()
        return await _get_plan_for_period(telegram_user_id, start, end, f"План на следующий месяц ({start} — {end})")

    if action == "show_workout_on_date":
        target_date = parsed.get("date") or date.today().isoformat()
        data = await get_today_planned_workout(telegram_user_id, target_date)
        if not data:
            return f"На {format_human_date(target_date)} активная плановая тренировка не найдена."
        return f"Тренировка на {format_human_date(target_date)}:\n\n" + format_planned_workout(data)

    if action == "replace_today_workout":
        return await _create_or_replace_today_workout(telegram_user_id, text, parsed)

    if action == "add_custom_workout":
        return await _add_custom_workout(telegram_user_id, text, parsed)

    if action == "log_workout_sets":
        return await _log_workout_sets(telegram_user_id, text, parsed, active_session)

    if action == "continue_current_exercise":
        return await _continue_current_exercise(telegram_user_id, text, parsed, active_session)

    if action == "delete_last_set":
        return await _delete_last_set(telegram_user_id, active_session)

    if action == "correct_previous_action":
        return await _correct_previous_action(telegram_user_id, text, parsed, active_session)

    if action == "edit_plan":
        return await _edit_plan(telegram_user_id, text, parsed)

    if action == "show_progress":
        return await _show_progress(telegram_user_id, text, parsed)

    if action == "add_note":
        return await _add_note(telegram_user_id, text, parsed)

    if action == "import_program":
        return await _import_program(telegram_user_id, text, parsed)

    if action == "export_workouts":
        return await _export_workouts(telegram_user_id)

    if action == "dangerous_delete":
        return (
            "Это опасное действие, я не буду удалять историю тренировок автоматически.\n\n"
            "Я могу безопасно сделать одно из двух:\n"
            "- очистить только план текущей недели: /fitness_reset_week\n"
            "- отменить конкретную плановую тренировку\n\n"
            "Фактическую историю тренировок без отдельного жёсткого подтверждения не трогаю."
        )

    # ── Новые экшены: архив, рекорды, замеры, завершение, помощь ──────────
    if action == "show_yesterday_workout":
        yesterday = _shift_date(date.today(), -1).isoformat()
        return await _show_completed_or_planned_for_date(telegram_user_id, yesterday)

    if action == "show_tomorrow_workout":
        tomorrow = _shift_date(date.today(), 1).isoformat()
        return await _show_completed_or_planned_for_date(telegram_user_id, tomorrow)

    if action == "show_last_workout":
        last = await get_last_workout(telegram_user_id)
        return format_last_workout(last)

    if action == "show_completed_day":
        target_date = parsed.get("date") or date.today().isoformat()
        workouts = await get_completed_workouts_in_period(telegram_user_id, target_date, target_date)
        return format_completed_period(workouts, f"Тренировки за {format_human_date(target_date)}")

    if action == "show_completed_week":
        start, end = week_bounds()
        period = parsed.get("period") or {}
        s = period.get("start_date") or start
        e = period.get("end_date") or end
        workouts = await get_completed_workouts_in_period(telegram_user_id, s, e)
        return format_completed_period(workouts, f"Тренировки за неделю ({format_human_date(s, include_weekday=False)} — {format_human_date(e, include_weekday=False)})")

    if action == "show_completed_month":
        start, end = month_bounds()
        period = parsed.get("period") or {}
        s = period.get("start_date") or start
        e = period.get("end_date") or end
        workouts = await get_completed_workouts_in_period(telegram_user_id, s, e)
        return format_completed_period(workouts, f"Тренировки за месяц ({format_human_date(s, include_weekday=False)} — {format_human_date(e, include_weekday=False)})")

    if action == "show_completed_period":
        period = parsed.get("period") or {}
        s = period.get("start_date")
        e = period.get("end_date")
        if not s or not e:
            return "Уточни период: с какой по какую дату показать тренировки."
        workouts = await get_completed_workouts_in_period(telegram_user_id, s, e)
        return format_completed_period(workouts, f"Тренировки {format_human_date(s, include_weekday=False)} — {format_human_date(e, include_weekday=False)}")

    if action == "show_personal_records":
        records = await get_personal_records(telegram_user_id, limit=30)
        return format_personal_records(records)

    if action == "show_exercise_stats":
        # Re-use _show_progress (it already handles exercise_name in target)
        return await _show_progress(telegram_user_id, text, parsed)

    if action == "finish_workout":
        if active_session and active_session.get("workout_id"):
            pending = await get_latest_fitness_pending_decision(telegram_user_id)
            if pending and pending.get("id"):
                await resolve_fitness_pending_decision(pending["id"], status="resolved")
            wid = active_session["workout_id"]
            return f"✅ Тренировка завершена (ID: {wid}). Можешь посмотреть отчёт командой «что я сделал сегодня»."
        return "Активная тренировочная сессия не найдена. Возможно, она уже завершена."

    if action == "record_measurement":
        return await _record_measurement(telegram_user_id, parsed)

    if action == "move_workout":
        return await _move_or_copy_workout(telegram_user_id, parsed, copy=False)

    if action == "copy_workout":
        return await _move_or_copy_workout(telegram_user_id, parsed, copy=True)

    if action == "help":
        return _help_text()

    if action == "show_learned_rules":
        return await _show_learned_rules(telegram_user_id)

    if action == "show_next_workout":
        from app.db import get_next_planned_workout
        nxt = await get_next_planned_workout(telegram_user_id)
        if not nxt:
            return "Дальше по плану ничего нет — добавь тренировки или импортируй программу."
        return "Следующая тренировка:\n\n" + format_planned_workout(nxt)

    if action == "compare_weeks":
        return await _compare_weeks(telegram_user_id)

    if action == "quick_stats":
        return await _quick_stats(telegram_user_id)

    if action == "edit_last_set":
        return await _edit_last_set(telegram_user_id, parsed, active_session)

    if action == "non_fitness":
        # Делегируем общему AI-ответчику, не ломая активную сессию
        from app.ai import generate_general_answer
        return await generate_general_answer(text)

    return None


def _shift_date(d: date, days: int) -> date:
    from datetime import timedelta
    return d + timedelta(days=days)


async def _show_completed_or_planned_for_date(telegram_user_id: str | None, target_date: str) -> str:
    """Show what was completed on date (if any), or what's planned."""
    completed = await get_completed_workouts_in_period(telegram_user_id, target_date, target_date)
    if completed:
        return format_completed_period(completed, f"Тренировка {format_human_date(target_date)}")
    planned = await get_today_planned_workout(telegram_user_id, target_date)
    if planned:
        return f"План на {format_human_date(target_date)}:\n\n" + format_planned_workout(planned)
    return f"На {format_human_date(target_date)} — ни выполненных, ни запланированных тренировок."


async def _record_measurement(telegram_user_id: str | None, parsed: dict) -> str:
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    m = parsed.get("measurement") or {}
    measurement_date = parsed.get("date") or date.today().isoformat()

    if not any(v is not None for v in m.values() if not isinstance(v, str)):
        return "Не понял, какие замеры записать. Пример: «вес 80.5, талия 82, % жира 15»."

    async with get_session() as session:
        result = await session.execute(sql_text("""
            INSERT INTO body_measurements
              (telegram_user_id, measurement_date, weight_kg, waist_cm, chest_cm,
               hips_cm, arm_cm, thigh_cm, neck_cm, bodyfat_pct, notes)
            VALUES
              (:uid, :d, :w, :waist, :chest, :hips, :arm, :thigh, :neck, :bf, :notes)
            RETURNING id
        """), {
            "uid": telegram_user_id,
            "d": measurement_date,
            "w": m.get("weight_kg"),
            "waist": m.get("waist_cm"),
            "chest": m.get("chest_cm"),
            "hips": m.get("hips_cm"),
            "arm": m.get("arm_cm"),
            "thigh": m.get("thigh_cm"),
            "neck": m.get("neck_cm"),
            "bf": m.get("bodyfat_pct"),
            "notes": m.get("notes"),
        })
        mid = result.scalar()
        await session.commit()

    parts = [f"Записал замеры (ID: {mid}, {format_human_date(measurement_date)}):"]
    for key, label, unit in [
        ("weight_kg", "Вес", "кг"), ("waist_cm", "Талия", "см"),
        ("chest_cm", "Грудь", "см"), ("hips_cm", "Бёдра", "см"),
        ("arm_cm", "Рука", "см"), ("thigh_cm", "Бедро", "см"),
        ("neck_cm", "Шея", "см"), ("bodyfat_pct", "% жира", "%"),
    ]:
        if m.get(key) is not None:
            parts.append(f"  {label}: {m[key]} {unit}")
    return "\n".join(parts)


async def _move_or_copy_workout(telegram_user_id: str | None, parsed: dict, copy: bool) -> str:
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    target = parsed.get("target") or {}
    from_date = target.get("from_date")
    to_date = target.get("to_date")
    if not from_date or not to_date:
        return "Уточни даты: с какой на какую перенести/скопировать."

    async with get_session() as session:
        src = await session.execute(sql_text("""
            SELECT id, title, focus, focus_label, workout_type, notes, plan_id
            FROM planned_workouts
            WHERE telegram_user_id = :uid AND planned_date = :d AND status = 'planned'
            ORDER BY id DESC LIMIT 1
        """), {"uid": telegram_user_id, "d": from_date})
        row = src.mappings().first()
        if not row:
            return f"На {format_human_date(from_date)} не нашёл активной плановой тренировки."

        if copy:
            new_id = await session.execute(sql_text("""
                INSERT INTO planned_workouts
                  (telegram_user_id, planned_date, title, focus, focus_label,
                   workout_type, status, notes, plan_id, source_text)
                SELECT telegram_user_id, :to_d, title, focus, focus_label,
                       workout_type, 'planned', notes, plan_id, 'copy_from_' || :sid::text
                FROM planned_workouts WHERE id = :sid
                RETURNING id
            """), {"to_d": to_date, "sid": row["id"]})
            target_id = new_id.scalar()
            await session.execute(sql_text("""
                INSERT INTO planned_exercises
                  (planned_workout_id, exercise_order, exercise_name,
                   target_sets, target_reps_min, target_reps_max, target_reps_text,
                   target_weight_kg, notes)
                SELECT :nid, exercise_order, exercise_name,
                       target_sets, target_reps_min, target_reps_max, target_reps_text,
                       target_weight_kg, notes
                FROM planned_exercises WHERE planned_workout_id = :sid
            """), {"nid": target_id, "sid": row["id"]})
            await session.commit()
            return f"📋 Скопировал тренировку с {format_human_date(from_date)} на {format_human_date(to_date)}."
        else:
            await session.execute(sql_text("""
                UPDATE planned_workouts
                SET planned_date = :to_d, status = 'planned'
                WHERE id = :sid
            """), {"to_d": to_date, "sid": row["id"]})
            await session.commit()
            return f"📅 Перенёс тренировку с {format_human_date(from_date)} на {format_human_date(to_date)}."


async def _show_learned_rules(telegram_user_id: str | None) -> str:
    from app.db import get_active_corrections, get_user_preferences
    rules = await get_active_corrections(telegram_user_id, scope="fitness", limit=50)
    prefs = await get_user_preferences(telegram_user_id)

    if not rules and not prefs:
        return (
            "Пока ничего не выучил.\n\n"
            "Чтобы научить меня: после моего ответа скажи «понял неправильно: ...» "
            "или «когда я говорю X — это Y». Я запомню и применю в следующих ответах."
        )

    lines = ["🧠 Что я выучил:", ""]
    if rules:
        lines.append("Правила парсинга/поведения:")
        for r in rules:
            pattern = (r.get("rule_pattern") or "")[:80]
            action = (r.get("rule_action") or "")[:100]
            lines.append(f"  #{r['id']} [{r['correction_type']}]")
            lines.append(f"    если: {pattern}")
            lines.append(f"    тогда: {action}")
        lines.append("")
    if prefs:
        lines.append("Предпочтения:")
        for k, v in prefs.items():
            lines.append(f"  • {k} = {v}")
    lines.append("")
    lines.append("Отключить правило: «забудь правило #N»")
    return "\n".join(lines)


async def _compare_weeks(telegram_user_id: str | None) -> str:
    """Compare this week's volume vs previous week."""
    from datetime import timedelta
    cur_start, cur_end = week_bounds()
    cur_start_d = datetime.strptime(cur_start, "%Y-%m-%d").date()
    prev_start = (cur_start_d - timedelta(days=7)).isoformat()
    prev_end = (cur_start_d - timedelta(days=1)).isoformat()

    cur = await get_completed_workouts_in_period(telegram_user_id, cur_start, cur_end)
    prev = await get_completed_workouts_in_period(telegram_user_id, prev_start, prev_end)

    def stats(workouts):
        total_sets = 0
        total_tonnage = 0.0
        for w in workouts:
            for s in (w.get("sets") or []):
                total_sets += 1
                try:
                    if s.get("weight_kg") and s.get("reps"):
                        total_tonnage += float(s["weight_kg"]) * int(s["reps"])
                except Exception:
                    pass
        return len(workouts), total_sets, round(total_tonnage, 1)

    cw, cs, ct = stats(cur)
    pw, ps, pt = stats(prev)

    def diff(a, b, unit=""):
        if b == 0:
            return f"{a}{unit} (на прошлой неделе 0)"
        delta = a - b
        sign = "▲" if delta > 0 else ("▼" if delta < 0 else "≈")
        pct = round((a - b) / b * 100, 1) if b else 0
        return f"{a}{unit} vs {b}{unit} ({sign} {delta:+}{unit}, {pct:+}%)"

    lines = [
        f"Сравнение недель:",
        f"  {format_human_date(prev_start, include_weekday=False)} — {format_human_date(prev_end, include_weekday=False)}  vs",
        f"  {format_human_date(cur_start, include_weekday=False)} — {format_human_date(cur_end, include_weekday=False)}",
        "",
        f"Тренировок: {diff(cw, pw)}",
        f"Подходов: {diff(cs, ps)}",
        f"Тоннаж: {diff(ct, pt, ' кг')}",
    ]
    return "\n".join(lines)


async def _quick_stats(telegram_user_id: str | None) -> str:
    """Quick overview: today's plan, this week's done, last workout, next workout."""
    today = date.today().isoformat()
    today_plan = await get_today_planned_workout(telegram_user_id, today)
    week_start, week_end = week_bounds()
    week_done = await get_completed_workouts_in_period(telegram_user_id, week_start, week_end)
    last = await get_last_workout(telegram_user_id)
    from app.db import get_next_planned_workout
    nxt = await get_next_planned_workout(telegram_user_id)

    lines = ["📊 Быстрая сводка:", ""]
    if today_plan:
        title = today_plan["workout"].get("title") or today_plan["workout"].get("focus_label") or "Тренировка"
        ex_count = len(today_plan.get("exercises") or [])
        lines.append(f"• Сегодня по плану: {title} ({ex_count} упр.)")
    else:
        lines.append("• Сегодня: плана нет")

    lines.append(f"• Сделано на этой неделе: {len(week_done)} тренировок")

    if last:
        lw = last["workout"]
        lines.append(f"• Последняя: {format_human_date(lw.get('workout_date'))} — {lw.get('focus_label') or lw.get('focus') or '—'}")

    if nxt:
        nd = nxt["workout"].get("planned_date")
        title = nxt["workout"].get("title") or nxt["workout"].get("focus_label") or "Тренировка"
        lines.append(f"• Следующая: {format_human_date(nd)} — {title}")

    return "\n".join(lines)


async def _edit_last_set(
    telegram_user_id: str | None,
    parsed: dict,
    active_session: dict | None,
) -> str:
    """Edit weight/reps of the last (or specific) set in the active workout."""
    if not active_session or not active_session.get("workout_id"):
        # Try to use last completed workout
        last = await get_last_workout(telegram_user_id)
        if not last:
            return "Не нашёл активной сессии и записанных тренировок для исправления."
        workout_id = last["workout"]["id"]
    else:
        workout_id = int(active_session["workout_id"])

    correction = parsed.get("correction") or {}
    field = correction.get("field") or "weight_kg"
    new_value = correction.get("new_value")
    if new_value is None:
        return "Не понял что менять. Скажи, например: «не 80, а 85», «поменяй последний на 90×6»."

    target = parsed.get("target") or {}
    set_number = target.get("set_number")

    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    async with get_session() as session:
        if set_number:
            row = await session.execute(sql_text("""
                SELECT id, exercise_name, weight_kg, reps
                FROM fitness_exercise_sets
                WHERE workout_id = :wid AND set_number = :sn
                ORDER BY id DESC LIMIT 1
            """), {"wid": workout_id, "sn": int(set_number)})
        else:
            row = await session.execute(sql_text("""
                SELECT id, exercise_name, weight_kg, reps
                FROM fitness_exercise_sets
                WHERE workout_id = :wid
                ORDER BY id DESC LIMIT 1
            """), {"wid": workout_id})
        rec = row.mappings().first()
        if not rec:
            return "Не нашёл подход для правки."

        if field == "reps":
            await session.execute(
                sql_text("UPDATE fitness_exercise_sets SET reps = :v WHERE id = :id"),
                {"v": int(new_value), "id": rec["id"]},
            )
        else:
            await session.execute(
                sql_text("UPDATE fitness_exercise_sets SET weight_kg = :v WHERE id = :id"),
                {"v": float(new_value), "id": rec["id"]},
            )
        await session.commit()

    return (
        f"✏️ Поправил: {rec['exercise_name']}, подход {set_number or 'последний'}, "
        f"{field}={new_value} (было: {rec['weight_kg']} кг × {rec['reps']})."
    )


def _help_text() -> str:
    return (
        "Я фитнес-ассистент Егора. Что я умею:\n\n"
        "📋 ПЛАНЫ:\n"
        "• «что сегодня по плану», «план на неделю», «что на следующей»\n"
        "• «что в пятницу», «что 20-го»\n"
        "• «сегодня делаем плечи вместо ног» — замена\n"
        "• «добавь жим в план на четверг», «убери присед из вторника»\n"
        "• «перенеси пятницу на среду»\n\n"
        "🏋️ ЗАПИСЬ ТРЕНИРОВКИ:\n"
        "• «запиши тренировку: жим 4×10 80кг, потом бицепс 25×15×4...»\n"
        "• Можешь диктовать по одному подходу — я пойму, что это продолжение\n"
        "• Поддерживаю суперсеты, дроп-сеты, RPE, AMRAP, пирамиды\n"
        "• «закончил тренировку» — закрыть сессию\n\n"
        "📊 АРХИВ И ПРОГРЕСС:\n"
        "• «что я сделал сегодня/вчера/на этой неделе/за месяц»\n"
        "• «последняя тренировка»\n"
        "• «история жима», «прогресс по приседу»\n"
        "• «мои рекорды», «ПР»\n\n"
        "📏 ЗАМЕРЫ:\n"
        "• «вес 80.5», «талия 82, грудь 100, % жира 15»\n\n"
        "📤 ЭКСПОРТ:\n"
        "• «выгрузи мои тренировки»"
    )


def _looks_like_fitness_text(text: str) -> bool:
    """Heuristic: any obvious fitness markers in the message."""
    t = text.lower().replace("ё", "е")
    keywords = [
        "тренировк", "упражнен", "подход", "повтор", "разминк",
        "жим", "тяга", "присед", "становая", "бицепс", "трицепс",
        "плечи", "грудь", "спина", "ноги", "ягодиц", "дельта",
        "штанг", "гантел", "блок", "канат", "тренаж", "брус",
        "подтяг", "отжим", "пресс", "икр",
        "рпе", "rpe", "rir", "amrap", "дроп",
        "суперсет", "пирамид", "тоннаж", "рекорд",
        " пр", " pr", "1пм", "1rm",
        "вес тела", "талия", "замер", "% жира",
        "сделал", "сделала", "записать", "запиши",
        "план", "программа", "график",
        "кг ", " кг", "×", "x ", " x", "повт",
    ]
    return any(k in t for k in keywords)
