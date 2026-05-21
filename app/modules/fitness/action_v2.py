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
    get_last_workout,
    get_last_measurement,
)
from app.bot_reply import BotReply
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.modules.fitness.exercise_history import handle_exercise_history_request
from app.modules.fitness.router_hardening import handle_router_hardening
from app.modules.fitness.self_learning import (
    handle_self_correction,
    handle_forget_request,
    build_corrections_context,
    is_correction_message,
    is_forget_message,
)
from app.db import (
    save_last_interaction, get_last_interaction,
    skip_planned_workout, shift_planned_workouts, cancel_plan_period,
    delete_last_n_sets, delete_last_exercise_from_workout, delete_workout,
    rename_exercise_in_workout,
    save_workout_template, list_workout_templates, get_workout_template_by_name,
    mark_template_used,
    add_fitness_goal, list_active_goals, mark_goal_achieved,
    bulk_update_planned_exercises, find_workouts_by_exercise,
    copy_planned_period,
    schedule_reminder, list_pending_reminders, cancel_reminder,
    get_exercise_weight_stats, get_measurements_period,
)
from app.modules.fitness.muscle_groups import (
    classify_exercise, aggregate_by_group, estimate_1rm,
)
from app.modules.fitness.formatter import (
    format_planned_workout,
    format_period_plan,
    format_human_date,
    format_completed_period,
    format_last_workout,
    format_last_measurement,
    format_number,
)
from app.modules.fitness.utils import (
    week_bounds,
    next_week_bounds,
    month_bounds,
    next_month_bounds,
)


def _safe_json_loads(text: str) -> dict:
    # 1) direct
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2) raw_decode — берёт первый валидный JSON-объект, игнорирует хвост
    try:
        decoder = json.JSONDecoder()
        start = text.find("{")
        if start >= 0:
            obj, _ = decoder.raw_decode(text[start:])
            return obj
    except Exception:
        pass
    # 3) полная нарезка от { до последнего }
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
    except Exception:
        pass
    # 4) пустой объект вместо falling
    return {}


SESSION_TIMEOUT_HOURS = 6


def _active_session_context_from_pending(pending: dict | None) -> dict | None:
    """Return active session context if recent, None if stale (>6h)."""
    if not pending:
        return None
    if pending.get("decision_type") != "active_workout_session":
        return None
    ctx = pending.get("context_json") or {}

    # Check freshness
    last_act = ctx.get("last_activity_at") or ctx.get("last_training_activity_at")
    if last_act:
        try:
            ts = datetime.fromisoformat(last_act.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_hours = (now - ts).total_seconds() / 3600
            if age_hours > SESSION_TIMEOUT_HOURS:
                ctx["_dormant"] = True
                ctx["_age_hours"] = round(age_hours, 1)
        except Exception:
            pass
    return ctx


async def _validate_active_session_or_resolve(
    telegram_user_id: str | None,
    pending: dict | None,
) -> dict | None:
    """Если pending указывает на workout с completion_type != 'active_session',
    значит это орфанный pending (finished, но pending не resolved). Resolveим и возвращаем None.
    Иначе возвращаем context из pending как есть.
    """
    if not pending:
        return None
    ctx = _active_session_context_from_pending(pending)
    if not ctx:
        return None
    wid = ctx.get("workout_id")
    if not wid:
        return ctx
    try:
        from app.db.engine import get_session
        from sqlalchemy import text as sql_text
        async with get_session() as s:
            r = await s.execute(
                sql_text("SELECT completion_type FROM fitness_workouts WHERE id = :id"),
                {"id": int(wid)},
            )
            row = r.first()
        if row and row[0] not in ("active_session", None):
            # Workout finished but pending still pending — resolve and ignore
            try:
                await resolve_fitness_pending_decision(pending["id"], status="resolved")
            except Exception:
                pass
            return None
    except Exception:
        pass
    return ctx


def _is_dormant(session: dict | None) -> bool:
    return bool(session and session.get("_dormant"))


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

        # Если есть numeric reps_min/max, очищаем дублирующий reps_text типа "4 по 10"
        if reps_text and (reps_min is not None or reps_max is not None):
            t_lower = str(reps_text).lower()
            if re.search(r"\d+\s*(по|на|x|×)\s*\d+", t_lower):
                reps_text = None

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


_WEEKDAY_TOKENS = _WEEKDAYS_RU + [
    # Короткие формы — отдельным регексом со словарными границами
]
_WEEKDAY_SHORT_RE = re.compile(r"\b(пн|вт|ср|чт|пт|сб|вс)\b", re.IGNORECASE)


def _looks_like_weekly_plan(text: str) -> bool:
    """Detect multi-day plan: several day-name headers OR explicit dates."""
    t = text.lower()
    days_found = sum(1 for d in _WEEKDAYS_RU if d in t)
    # Короткие формы дней недели (пн/вт/ср/чт/пт/сб/вс)
    days_found += len(_WEEKDAY_SHORT_RE.findall(text))
    # Явные даты вида "25-05-2026", "25/05/2026", "25.05.2026"
    explicit_dates = len(re.findall(r"\b\d{1,2}[-./]\d{1,2}[-./]\d{2,4}\b", text))
    # ISO: 2026-05-25
    iso_dates = len(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text))

    has_numbered = bool(re.search(r"^\s*\d+\.", text, re.MULTILINE))
    has_sets_pattern = bool(re.search(r"\d+\s*[×x✕]\s*\d+", text))
    has_kg = "кг" in t
    has_reps_marker = any(x in t for x in ["повторен", "подход"])
    has_exercises = has_numbered or has_sets_pattern or has_kg or has_reps_marker

    # 3+ weekdays with exercises
    if days_found >= 3 and has_exercises:
        return True
    # 3+ explicit dates with exercises
    if (explicit_dates + iso_dates) >= 3 and has_exercises:
        return True
    # 2 weekdays + numbered exercises
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

    try:
        response = await claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text if response.content else "{}"
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).exception("parse_complex_workout_plan claude call failed: %s", e)
        return {"action": "unknown", "error": str(e)[:200], "workout": {"exercises": []}}

    try:
        return _safe_json_loads(raw)
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).exception("parse_complex_workout_plan JSON decode failed: %s", e)
        return {"action": "unknown", "error": f"json decode: {e}", "workout": {"exercises": []}}


async def parse_weekly_plan(text: str) -> dict:
    """Parse a multi-day weekly/monthly plan into multiple planned_workouts."""
    today = date.today().isoformat()
    week_start, week_end = week_bounds()
    next_start, next_end = next_week_bounds()

    system_prompt = f"""Ты парсер недельной/многодневной программы тренировок. Сегодня: {today}.
Текущая неделя: {week_start} — {week_end}. Следующая: {next_start} — {next_end}.

Распарси КАЖДЫЙ день как отдельную тренировку. Распарси ВСЕ упражнения. Ничего не теряй.

═══ ДАТЫ И ДНИ ═══
- ЯВНЫЕ ДАТЫ в шапках дней — ИМЕЮТ ПРИОРИТЕТ над названиями дней недели:
  • "25-05-2026 — понедельник" → planned_date="2026-05-25", weekday="monday"
  • "25.05.2026" / "25/05/2026" / "2026-05-25" → используй как planned_date в ISO формате
  • Парси формат дд-мм-гггг или дд.мм.гггг или дд/мм/гггг → ISO YYYY-MM-DD
- Заголовки БЕЗ дат: "Понедельник"/"Пн"/"ПН", "Вторник"/"Вт", "Среда"/"Ср", "Четверг"/"Чт", "Пятница"/"Пт", "Суббота"/"Сб", "Воскресенье"/"Вс"
- ⚠️ КОМПАКТНЫЙ формат через запятые/точки с запятой — поддерживается:
  "пн грудь жим 4×10 80кг, ср ноги присед 4×8 100кг, пт спина тяга 4×10 70кг"
  → 3 тренировки: пн/ср/пт, у каждой свой фокус и одно упражнение.
  "Запланируй неделю: пн A; ср B; пт C" → 3 тренировки.
  Разделители тренировок в компактном формате: ", " / "; " / новая строка.
- "День 1" / "Day 1" / "A" (в ABC/PPL/AB схемах) → если пользователь сказал "со следующего понедельника", раскладывай по порядку начиная с понедельника
- "Push / Pull / Legs" (PPL) → 3 тренировки, не привязаны к датам (planned_date=null, weekday=null), если не сказано иначе
- Если сказано "на следующую неделю" БЕЗ явных дат → planned_date в диапазоне {next_start} — {next_end}
- Если сказано "на этой неделе" БЕЗ явных дат → planned_date в диапазоне {week_start} — {week_end}
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

    try:
        response = await claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,  # длинные планы режутся на 4096
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text if response.content else "{}"
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).exception("parse_weekly_plan claude call failed: %s", e)
        return {"action": "unknown", "error": str(e)[:200], "plan": {"planned_workouts": []}}

    try:
        return _safe_json_loads(raw)
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).exception("parse_weekly_plan JSON decode failed: %s, raw=%r", e, raw[:500])
        return {"action": "unknown", "error": f"json decode: {e}", "plan": {"planned_workouts": []}}


async def _save_weekly_plan(telegram_user_id: str | None, text: str, parsed: dict) -> str:
    plan_data = parsed.get("plan") or {}
    planned_workouts_raw = plan_data.get("planned_workouts") or []

    if not planned_workouts_raw:
        # Fallback: сохраним как свободный план чтобы пользователь не остался без ответа
        plan_id = await save_training_plan(
            telegram_user_id=telegram_user_id,
            plan_name="Недельная программа (сырой текст)",
            period_type="week",
            start_date=None,
            end_date=None,
            source_text=text,
            notes="Авто-парсер не разложил по дням — сохранено как сырой текст",
            planned_workouts=[],
        )
        return (
            f"⚠️ Не смог автоматически разложить программу по дням (ID плана: {plan_id}).\n"
            f"Сохранил полный текст. Если хочешь — пришли план по одному дню за раз "
            f"(«Понедельник: жим 4×10 80кг, разводка 3×12...»), я создам структурированные тренировки."
        )

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
    prev_context: dict | None = None,
) -> dict:
    today = date.today().isoformat()
    current_week_start, current_week_end = week_bounds()
    next_week_start, next_week_end = next_week_bounds()
    month_start, month_end = month_bounds()
    next_month_start, next_month_end = next_month_bounds()

    corrections_block = ""
    if telegram_user_id:
        try:
            corrections_block = await build_corrections_context(telegram_user_id)
        except Exception:
            corrections_block = ""

    # Previous conversation context — критично для разрешения "её", "оттуда", "там"
    prev_block = ""
    if prev_context:
        prev_date = prev_context.get("current_workout_date")
        prev_focus = prev_context.get("current_focus")
        prev_action = prev_context.get("action")
        prev_input = (prev_context.get("input_text") or "")[:200]
        if prev_date or prev_focus or prev_action:
            prev_block = (
                f"\n═══ Контекст предыдущего сообщения (используй для разрешения 'её/оттуда/там/эту') ═══\n"
                f"- Предыдущий вопрос пользователя: {prev_input!r}\n"
                f"- Обсуждаемая дата тренировки: {prev_date or '—'}\n"
                f"- Обсуждаемый фокус: {prev_focus or '—'}\n"
                f"- Предыдущий action: {prev_action or '—'}\n"
                f"ВАЖНО: если пользователь говорит 'её', 'эту', 'оттуда', 'там', 'меняем X на Y' "
                f"БЕЗ явной даты — используй дату из этого контекста, НЕ today.\n"
            )

    system_prompt = f"""
Ты главный parser фитнес-ассистента. Возвращай СТРОГО JSON без markdown.

Контекст:
- Сегодня: {today}
- Текущая неделя: {current_week_start} — {current_week_end}
- Следующая неделя: {next_week_start} — {next_week_end}
- Текущий месяц: {month_start} — {month_end}
- Следующий месяц: {next_month_start} — {next_month_end}
- Активная сессия: {json.dumps(active_session or {}, ensure_ascii=False)}
{prev_block}
{corrections_block}

═══ JSON-СХЕМА ═══
{{
  "action": "show_today_workout | show_yesterday_workout | show_tomorrow_workout | show_week_plan | show_next_week_plan | show_month_plan | show_next_month_plan | show_workout_on_date | show_last_workout | show_completed_day | show_completed_week | show_completed_month | show_completed_period | show_next_workout | quick_stats | replace_today_workout | add_custom_workout | log_workout_sets | continue_current_exercise | finish_workout | correct_previous_action | delete_last_set | edit_last_set | move_workout | copy_workout | edit_plan | show_progress | add_note | record_measurement | import_program | export_workouts | dangerous_delete | help | show_learned_rules | skip_workout | shift_plan | clear_plan_period | merge_workouts | delete_last_n_sets_action | delete_last_exercise | delete_workout_action | rename_last_exercise | mark_last_as_warmup | export_csv | show_last_recorded | undo_last | add_set_note | resume_session | tag_feeling | show_streak | save_template | apply_template | list_templates | bulk_edit_exercises | set_goal | show_goals | coach_report | weekly_summary | copy_week_to_next | copy_period_to_period | schedule_reminder_action | list_reminders | cancel_reminder_action | non_fitness | unknown | clarify",
  "confidence": 0.0,
  "date": null,
  "weekday": null,
  "period": {{"start_date": null, "end_date": null, "period_type": null}},
  "target": {{
    "focus": null,
    "focus_label": null,
    "exercise_name": null,
    "new_exercise_name": null,
    "set_number": null,
    "note_text": null,
    "from_date": null,
    "to_date": null,
    "merge_into_date": null,
    "shift_days": null,
    "n_sets_to_delete": null,
    "body_part": null,
    "severity": null,
    "constraint_id": null,
    "constraint_until": null,
    "export_format": null,
    "feeling": null,
    "template_name": null,
    "goal_value": null,
    "goal_deadline": null,
    "goal_type": null,
    "weight_delta": null,
    "weight_set_to": null,
    "sets_set_to": null,
    "pain_severity": null,
    "days": null,
    "muscle_group": null,
    "time_hh_mm": null,
    "recurrence": null
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
  "measurement": {{"weight_kg": null, "neck_cm": null, "chest_cm": null, "arm_cm": null, "belly_cm": null, "waist_cm": null, "hips_cm": null, "thigh_cm": null, "calf_cm": null, "notes": null}},
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

⚠️ КРИТИЧНО — РАЗЛИЧАЙ ПЛАН (запланированное) и АРХИВ (то что уже сделано):

🔴 АРХИВ (show_completed_day/week/month) — обязательно если есть глагол "ДЕЛАЛ/СДЕЛАЛ/ЗАПИСАЛ" в прошлом времени:
- "что я делал сегодня" → show_completed_day  (date=today)
- "что я делал вчера" → show_completed_day  (date=yesterday)
- "что я сделал на этой неделе" → show_completed_week
- "что я сделал в этом месяце" → show_completed_month
- "покажи мои записи за сегодня" → show_completed_day
- "что я записал" → show_completed_day
⚠️ НИ В КОЕМ СЛУЧАЕ не show_workout_on_date или show_today_workout если есть "ДЕЛАЛ/СДЕЛАЛ/ЗАПИСАЛ".

🟢 ПЛАН (show_today_workout / show_workout_on_date / show_week_plan):
- "что у меня сегодня" / "что сегодня по плану" / "план на сегодня" → show_today_workout
- "что у меня в [день недели]" → show_workout_on_date
- "план на неделю" → show_week_plan
Маркер ПЛАНА: НЕТ глагола "делал/сделал/записал". Просто запрос показа.

Если ТЕКСТ имеет "что я делал/сделал" + время — ВСЕГДА архив.
Если ТЕКСТ имеет "что у меня/что сегодня/план на" БЕЗ глагола делал — ПЛАН.

Также:
- "что я делал вчера", "вчерашняя выполненная тренировка" → show_completed_day с date=вчера
- "что я делал 15-го", "что было 12 мая" → show_completed_day с явной date
- "что я делал вчера", "вчерашняя выполненная тренировка" → show_completed_day с date=вчера
- "что я делал 15-го", "что было 12 мая" → show_completed_day с явной date
- "что я сделал на этой неделе", "тренировки за неделю", "отчёт за неделю", "сводка за неделю" → show_completed_week
- "что я делал в прошлую неделю", "тренировки прошлой недели" → show_completed_period с period.start_date/end_date
- "что я сделал в этом месяце", "отчёт за месяц", "сводка за месяц", "тренировки месяца" → show_completed_month
- "за период с X по Y", "с понедельника по пятницу", "с 1 по 15 мая" → show_completed_period
- "что дальше", "следующая тренировка", "когда след треня", "что у меня дальше по плану" → show_next_workout
- "сводка", "быстрая статистика", "что у меня", "статус", "кратко" (без указания периода) → quick_stats

2. ЗАМЕНА сегодняшней (replace_today_workout):
- "сегодня вместо ног делаем плечи"
- "замени сегодняшнюю на плечи"
- "поставь на сегодня плечи"
- "сегодня тренировка: жим гантелей, разводка, фронтальный подъём" → replace_today_workout, заполни workout.exercises

3. ДОБАВЛЕНИЕ (add_custom_workout):
- "добавь тренировку на сегодня/завтра/пятницу", "поставь тренировку на ...", "запиши тренировку на завтра"
- "Поставь на пятницу/субботу/понедельник [фокус]: упр1, упр2, ..." → add_custom_workout
  с workout.exercises и parsed.date вычисли = ближайшая такая дата
  ВАЖНО: "Поставь на [день недели] X: Y" — это ДОБАВЛЕНИЕ нового плана на тот день,
  НЕ перемещение и НЕ замена сегодняшней.
- ⚠️ "Поставь на [день] упр1, упр2" → add_custom_workout (создаёт новую тренировку на тот день)
  - Это НЕ edit_plan, НЕ move_workout
  - Если на тот день плана ещё нет — создаём новый
  - Если уже есть — заменяем (replace) или уточняем у пользователя
  - Примеры: "Поставь на среду жим и присед", "Поставь на четверг: грудь — жим, разводка"
    → add_custom_workout с workout.exercises и date = ближайший указанный день недели

4. ЗАПИСЬ ФАКТА (log_workout_sets):
КЛЮЧЕВОЕ — распознавай ВСЕ варианты:
- "записываем сегодняшнюю тренировку..."
- "жим: 80×5, 80×5, 75×8" / "жим 80 на 5, 80 на 5, 75 на 8"
- "присед 4 подхода 100×5" / "присед 4×5 100кг" / "100 на 5 четыре раза"
- ⚠️ ТРИ числа через × это weight×reps×sets:
  "30×15×2" → 2 подхода 30кг×15повт (вес=30, reps=15, sets=2)
  "100×5×3" → 3 подхода 100кг×5повт
  "22×12×3" → 3 подхода 22кг×12повт
  "Сведение 30×15×2" → exercise="Сведение", 2 подхода 30×15
- ⚠️ ВРЕМЕННЫЕ упражнения (планка, статика, удержание):
  "Планка 60 секунд 3 подхода" → 3 подхода, каждый duration_seconds=60, reps=null, weight_kg=null
  "Планка 2 минуты" → 1 подход, duration_seconds=120
  "Удержание на 30 сек × 4 подхода" → 4 подхода duration_seconds=30 каждый
  "Вакуум 20 секунд 5 раз" → 5 подходов duration_seconds=20
  Если ВРЕМЯ есть — обязательно положи в duration_seconds, не путай со sets.
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

6b. ⚠️ КРИТИЧНО — РАЗЛИЧАЙ ФАКТ vs ПРАВКУ ПЛАНА:

ФАКТ (log_workout_sets) — пользователь РАССКАЗЫВАЕТ что СДЕЛАЛ:
- "сделал X 25 кг 12 раз"
- "выполнил жим 80 на 10"
- "первый подход пуловер 25×12"
- "разминка: 20 на 10"
- "последний подход 90 на 8, перехожу к следующему упражнению"
- "пуловер с канатами 25 килограмм 12 повторений" (в контексте текущей сессии)

ПРАВКА ПЛАНА (edit_plan / operation=update) — пользователь ИЗМЕНЯЕТ план будущей тренировки:
- "поставь 90 кг в жиме на пятницу"
- "увеличь вес жима на 5 кг"
- "сделай 4 по 8 в плане"
- "измени план жима лёжа на 5×5"

Ключевые маркеры ФАКТА: "сделал/выполнил/завершил" (прош. время своего действия) + число + ("кг"/"раз"/"повтор"/"подход").
Ключевые маркеры ПЛАНА: "поставь/измени/увеличь/сократи" (повелительное наклонение к боту).

Если сомневаешься — выбирай log_workout_sets, особенно когда дата = сегодня.

6c. "Начинаю тренировку" / "стартую сессию" — НЕ просмотр плана!
- "начинаю делать сегодняшнюю", "стартую тренировку", "приступаю",
  "запускаю активную сессию", "первый подход X" (без чисел) → action="log_workout_sets"
  с logged_exercises=[] если упражнение не названо, ИЛИ с упражнением но без подходов.
  Если нет числа подходов — confidence=0.6, summary="старт сессии, ждём подходы".

7. РЕДАКТИРОВАНИЕ ПЛАНА (edit_plan) — confidence >= 0.8 даже если детали не извлечены:
- "добавь жим в сегодняшнюю", "вставь тягу в план на четверг"
- "убери присед из вторника", "удали жим из плана"
- "замени жим лёжа на жим гантелей", "поменяй штангу на гантели"
- "увеличь вес в жиме до 90", "поставь 4 подхода вместо 3"
- "Поменяй вес жима на 90" → edit_plan + target.exercise_name="жим"
- "Сделай жим на 4 по 10" → edit_plan + target.exercise_name="жим"
- "Поставь в жиме 4 подхода" → edit_plan
- "измени план", "давай поправим", "давай её изменим", "хочу поменять",
  "хочу убрать оттуда", "оттуда убери", "из неё убери", "там вместо X сделаем Y"
- "поменяй трицепс канат на трицепс прямым грифом" — replace
- "убери трицепс из пятничной" — remove
- ⚠️ ДОБАВЛЕНИЕ УПРАЖНЕНИЯ В СУЩЕСТВУЮЩИЙ ПЛАН — это edit_plan (operation=add), НЕ add_custom_workout:
  - "добавь в план жим" → edit_plan, operation=add
  - "добавь X в план" → edit_plan, operation=add (НЕ add_custom_workout!)
  - "добавь в план X 3×8" → edit_plan, operation=add, с параметрами подходов
  - "добавь в сегодняшнюю X", "вставь X в план на [день]" → edit_plan, operation=add
  Отличие от add_custom_workout: здесь упражнение ДОБАВЛЯЕТСЯ к уже существующей тренировке в плане.
- ВАЖНО: если в тексте есть глагол "поменять/изменить/убрать/добавить/заменить" в адрес тренировки —
  это edit_plan, даже если дата только что обсуждалась (используй prev_context).
  ВСЕГДА предпочитай edit_plan, а не clarify, для таких глаголов.
- Дату НЕ оставляй null: возьми из prev_context.current_workout_date если в тексте даты нет.

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
- "талия 82, грудь 100", "замеры: вес 80, талия 82, рука 38"
- Multiline формат:
    Вес 102.3
    Голень 46
    Бедро 68
    Бедра 105
    Живот 98
    Талия 92
    Грудь 108
    Рука 40
    Шея 40
  → record_measurement, заполни measurement: weight_kg=102.3, calf_cm=46, thigh_cm=68,
  hips_cm=105, belly_cm=98, waist_cm=92, chest_cm=108, arm_cm=40, neck_cm=40.
  Маппинг: Вес→weight_kg, Голень→calf_cm, Бедро→thigh_cm, Бедра→hips_cm,
  Живот→belly_cm, Талия→waist_cm, Грудь→chest_cm, Рука→arm_cm/Бицепс→arm_cm,
  Шея→neck_cm.

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

14d. ПРОПУСКИ И СДВИГИ ПЛАНА (skip_workout / shift_plan / clear_plan_period / merge_workouts):
- "пропустил вчерашнюю", "не ходил вчера", "пометь пятницу как пропущенную" → skip_workout
  target.from_date = пропущенная дата (например, вчера)
- "сдвинь все тренировки на день вперёд", "перенеси оставшиеся на 2 дня"  → shift_plan
  target.from_date = с какой даты, target.shift_days = N
- "очисти план на эту неделю", "удали все плановые в мае", "сбрось план" → clear_plan_period
  period.start_date / period.end_date
- "объедини вторник и среду", "слей пятницу с субботой", "комбо: пн+чт" → merge_workouts
  target.from_date = откуда взять упражнения, target.merge_into_date = куда добавить

14e. ОТКАТ ЗАПИСИ (delete_last_n_sets_action / delete_last_exercise / delete_workout_action / rename_last_exercise / mark_last_as_warmup):
- "удали последний подход" → delete_last_set (уже есть)
- "удали последние 2 подхода", "снеси три последних" → delete_last_n_sets_action
  target.n_sets_to_delete=2 или 3
- "удали последнее упражнение", "убери всё что я записал на жим" → delete_last_exercise
- "удали всю тренировку", "сотри сегодняшнюю запись", "не хочу записывать" → delete_workout_action
- "это была не присед а жим ногами", "переименуй последнее на X" → rename_last_exercise
  target.exercise_name="старое имя или null если последнее",
  target.new_exercise_name="X"
- "это была разминка", "первый подход — разминка" → mark_last_as_warmup
  target.set_number=null или N

14g. КАЧЕСТВО ЖИЗНИ (show_last_recorded / undo_last / add_set_note / resume_session / tag_feeling):
- "что я только что записал", "покажи последнее", "повтори ответ", "что ты записал" → show_last_recorded
- "отмени последнее действие", "верни как было", "отмена", "undo" → undo_last
  Применяется ТОЛЬКО когда последний action писал в базу (log_workout_sets, edit_*, delete_*).
- "к подходу 3 заметка: помог напарник", "к последнему сету: спина устала",
  "пометь второй: с другим хватом" → add_set_note
  target.set_number=N, target.note_text="..."
- "продолжаю вчерашнюю тренировку", "продолжаю прерванную", "оживи сессию" → resume_session
- "сегодня сильный", "слабый день", "болело плечо после жима", "энергия 7/10",
  "общее состояние: устал", "сила 9/10", "форма норм" → tag_feeling
  target.feeling="сильный/слабый/болело/норм/устал" + опциональный numeric (7/10)

14f. ЭКСПОРТ В ФАЙЛ (export_csv):
- "выгрузи в CSV", "экспортируй в файл", "дай таблицу"
- "csv за май", "экспорт за период" → export_csv с period
- "json" → target.export_format="json"
- target.export_format = "csv" | "json" | "txt", по умолчанию csv

14h. СТРИК / ЧАСТОТА:
- "сколько дней подряд", "стрик", "сколько тренировок в этом месяце" → show_streak

14j. ШАБЛОНЫ И ПРОГРАММИРОВАНИЕ (Пакет 4):
- "сохрани как шаблон 'грудь A'", "запомни эту как шаблон X" → save_template
  target.template_name="имя"
- "примени шаблон 'грудь A' на завтра", "тренировка по шаблону 'ноги' сегодня" → apply_template
  target.template_name, parsed.date=когда
- "покажи шаблоны", "мои шаблоны", "список шаблонов" → list_templates
- "+5 кг ко всем жимам", "увеличь все приседы на 2.5кг" → bulk_edit_exercises
  target.exercise_name="жим/присед", target.weight_delta=5
- "сократи все жимы до 3 подходов" → bulk_edit_exercises, target.sets_set_to=3
- "поставь все жимы на 80кг" → bulk_edit_exercises, target.weight_set_to=80
- "цель: жим 100 кг к декабрю", "хочу присед 150 к лету" → set_goal
  target.exercise_name, target.goal_value=100, target.goal_deadline="2026-12-01"
- "мои цели", "прогресс к цели" → show_goals

14k. ОТЧЁТЫ (Пакет 5):
- "сделай отчёт тренеру", "текст для тренера", "отчёт за неделю красиво" → coach_report
  period.start_date / end_date (по умолчанию неделя)
- "сводка недели", "weekly", "weekly summary" → weekly_summary

14m. НАПОМИНАНИЯ:
- "напомни про тренировку в 7 утра", "ставь напоминание на 18:00 сегодня",
  "разбуди в 9", "напомни через час позвонить" → schedule_reminder_action
  target.note_text="текст напоминания", date=YYYY-MM-DD, target.time_hh_mm="HH:MM"
- "напоминай каждый день в 8 пить воду" → recurrence="daily", target.time_hh_mm="08:00"
- "напоминай по пн/ср/пт в 7 о тренировке" → recurrence="weekly:mon,wed,fri"
- "напомни про сегодняшнюю тренировку в 6 вечера" → kind="workout_today"
- "мои напоминания", "что у меня запланировано напомнить" → list_reminders
- "отмени напоминание #5" → cancel_reminder_action, target.constraint_id=5 (reusing field)

14n. ЗАМЕРЫ ТЕЛА (тренд):
- "график веса", "как меняется вес", "замеры за месяц", "тренд талии",
  "динамика веса", "вес за 90 дней" → show_measurements_trend
  target.days=N (по умолчанию 90)

14l. КОПИРОВАНИЕ ПЕРИОДОВ:
- "скопируй неделю на следующую", "копируй тренировки этой недели на след",
  "перенеси все тренировки этой недели в следующую", "продублируй неделю",
  "клонируй неделю" → copy_week_to_next
- "скопируй с 18.05 по 24.05 начиная с 01.06" → copy_period_to_period
  period.start_date / end_date = source, target.to_date = destination start
- НЕ путать со скиппами или сдвигами — здесь именно ДУБЛИРОВАНИЕ.

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

    try:
        response = await claude_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text if response.content else "{}"
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).exception("parse_fitness_action_v2 claude call failed: %s", e)
        return {"action": "unknown", "confidence": 0.0, "error": str(e)[:200]}

    try:
        return _safe_json_loads(raw)
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).exception("parse_fitness_action_v2 JSON decode failed: %s", e)
        return {"action": "unknown", "confidence": 0.0, "error": f"json: {e}"}


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


_STRONG_LOG_VERBS = [
    "я сделал", "я сделала", "я выполнил", "я выполнила", "я записал", "я записала",
    "сделал ", "сделала ", "выполнил ", "выполнила ", "залогируй", "записывай",
    "запиши тренировку", "запиши треню", "лог тренировки", "лог сессии",
    "сегодня сделал", "сегодня делал", "вчера сделал", "вчера делал",
    "сегодня в зале", "сегодня было", "вот моя тренировка", "вот тренировка",
    "записываем", "записываю",
]

_STRONG_PLAN_VERBS = [
    "запланируй", "запланир", "поставь на", "сделай мне план", "сделай план",
    "программа тренировок", "программу тренировок", "хочу запланир",
    "добавь тренировку на", "добавь в план", "поставь в план", "запиши план",
    "план на",
]


def _has_strong_log_signal(text: str) -> bool:
    t = (text or "").lower().replace("ё", "е")
    return any(v in t for v in _STRONG_LOG_VERBS)


def _has_strong_plan_signal(text: str) -> bool:
    t = (text or "").lower().replace("ё", "е")
    return any(v in t for v in _STRONG_PLAN_VERBS)


async def _maybe_ask_log_or_plan(
    telegram_user_id: str | None,
    text: str,
    parsed: dict,
) -> "str | BotReply | None":
    """Ask the user via inline buttons whether to log or plan an ambiguous workout block.

    Returns BotReply with buttons if ambiguous, None otherwise (caller continues normally).
    """
    # Если уже есть сильный маркер — не спрашиваем
    if _has_strong_log_signal(text) or _has_strong_plan_signal(text):
        return None

    # Должны быть упражнения хоть в каком-то виде
    workout = parsed.get("workout") or {}
    exes = workout.get("exercises") or []
    logged = parsed.get("logged_exercises") or []
    if not exes and not logged:
        return None

    # Создаём pending decision с распарсенными данными
    try:
        from app.db import create_fitness_pending_decision
        await create_fitness_pending_decision(
            telegram_user_id=telegram_user_id,
            decision_type="disambiguate_log_or_plan",
            context={"parsed": parsed, "source_text": text[:2000]},
            source_text=text[:500],
        )
    except Exception:
        return None

    # Формируем превью
    n_exes = len(exes) + len(logged)
    names = []
    for ex in (exes + logged)[:3]:
        names.append(ex.get("exercise_name") or "—")
    sample = ", ".join(names)
    if n_exes > 3:
        sample += f" и ещё {n_exes - 3}"
    target_date = parsed.get("date") or date.today().isoformat()
    try:
        date_human = format_human_date(target_date)
    except Exception:
        date_human = target_date

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📝 Записать (факт)", callback_data="fit:disamb_log"),
        InlineKeyboardButton("📅 Запланировать", callback_data="fit:disamb_plan"),
    ], [
        InlineKeyboardButton("❌ Отменить", callback_data="fit:disamb_cancel"),
    ]])

    return BotReply(
        text=(
            f"Не уверен — это уже сделанная тренировка или план на {date_human}?\n\n"
            f"Упражнения: {sample}\n\n"
            "📝 Записать — добавить как выполненный факт\n"
            "📅 Запланировать — добавить в план на дату"
        ),
        keyboard=keyboard,
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

    # Fallback для временных упражнений (Планка/Удержание/Вакуум):
    # если парсер дал имя но без подходов ИЛИ с пустыми подходами (без reps/weight),
    # и в тексте есть "X секунд N подходов" — синтезируем подходы где reps=секунды.
    if logged_exercises:
        for ex in logged_exercises:
            ex_name_low = (ex.get("exercise_name") or "").lower()
            if not any(k in ex_name_low for k in ["планка", "удержан", "вакуум", "статик"]):
                continue
            sets_list = ex.get("sets") or []
            # пустые подходы = нет ни веса, ни повторов
            all_empty = all(
                (s.get("weight_kg") is None and s.get("reps") is None)
                for s in sets_list
            )
            if sets_list and not all_empty:
                continue  # уже валидные сеты, не трогаем
            m_dur = re.search(r"(\d+)\s*(сек|секунд|с\b|минут|мин\b)", text.lower())
            m_count = re.search(r"(\d+)\s*(подход|раз)", text.lower())
            if m_dur:
                dur = int(m_dur.group(1))
                if m_dur.group(2) in ("минут", "мин"):
                    dur *= 60
                count = int(m_count.group(1)) if m_count else max(1, len(sets_list))
                ex["sets"] = [
                    {"set_number": i + 1, "weight_kg": None, "reps": dur, "notes": "секунды"}
                    for i in range(count)
                ]
    # Аналогичный fallback если упражнений нет вообще — детектим "планка X сек"
    if not logged_exercises:
        t_low = text.lower().replace("ё", "е")
        time_match = re.search(r"(планка|удержание|вакуум|статика)\s*(\d+)\s*(сек|секунд|с\b|минут|мин\b)", t_low)
        if time_match:
            name_low = time_match.group(1)
            dur = int(time_match.group(2))
            if time_match.group(3) in ("минут", "мин"):
                dur *= 60
            m_count = re.search(r"(\d+)\s*(подход|раз)", t_low)
            count = int(m_count.group(1)) if m_count else 1
            cap_name = name_low[:1].upper() + name_low[1:]
            logged_exercises = [{
                "exercise_name": cap_name,
                "sets": [
                    {"set_number": i + 1, "weight_kg": None, "reps": dur, "notes": "секунды"}
                    for i in range(count)
                ],
                "notes": None,
            }]

    if not logged_exercises:
        return "Я понял, что ты записываешь тренировку, но не смог уверенно выделить подходы."

    # ── Sanity check на сильные отклонения от обычных весов ──
    warnings = []
    skip_sanity = "проверено" in text.lower() or "точно" in text.lower()
    if not skip_sanity:
        for ex in logged_exercises:
            ex_name = ex.get("exercise_name") or ""
            for s in ex.get("sets") or []:
                w = s.get("weight_kg")
                if w is None:
                    continue
                try:
                    w = float(w)
                except Exception:
                    continue
                if w > 500 or w < 0:
                    warnings.append(f"⚠️ {ex_name}: вес {w} кг — нереалистично, проверь.")
                    continue
                stats = await get_exercise_weight_stats(telegram_user_id, ex_name, limit=10)
                avg = stats.get("avg_w")
                max_w = stats.get("max_w")
                n = stats.get("n") or 0
                if n >= 3 and avg and max_w:
                    avg_f = float(avg)
                    max_f = float(max_w)
                    # outlier: > 2× max или < 0.3× avg при достаточной истории
                    if w > max_f * 2:
                        warnings.append(
                            f"⚠️ {ex_name}: вес {w} кг, но твой максимум был {format_number(max_f)} кг. "
                            f"Возможно опечатка? Скажи «проверено» или повтори с правильным."
                        )
                    elif w < avg_f * 0.3:
                        warnings.append(
                            f"ℹ️ {ex_name}: вес {w} кг гораздо ниже обычного ({format_number(avg_f)} кг). "
                            f"Если разминка — это нормально, отметь как разминочный после записи."
                        )
        if any(w.startswith("⚠️") and "нереалистично" in w or "опечатка" in w for w in warnings):
            # Hard warning — don't save, ask for confirmation
            return "\n".join(warnings) + "\n\nЕсли всё верно — повтори: «проверено» в начале сообщения."

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
    lines.append("Продолжай диктовать, или жми кнопку чтобы завершить.")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Закончить тренировку", callback_data=f"fit:finish_workout:{workout_id}"),
    ]])
    return BotReply(text="\n".join(lines).strip(), keyboard=kb)


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

    # Запрос текущего состояния сессии ("покажи что записал", "покажи сессию" и т.п.)
    _show_session_phrases = [
        "покажи сессию", "покажи текущую", "покажи что записал",
        "что уже записал", "что я записал", "что в сессии",
        "сколько записал", "что у меня в сессии", "покажи тренировку",
        "что записано", "итог сессии", "текущие подходы",
    ]
    if any(p in normalized for p in _show_session_phrases):
        workout_id = active_session.get("workout_id")
        if workout_id:
            from app.db import get_last_workout
            wdata = await get_last_workout(telegram_user_id)
            if wdata and str(wdata["workout"].get("id")) == str(workout_id):
                sets = wdata.get("sets") or []
                if not sets:
                    return "📋 Сессия активна, подходов ещё не записано."
                # Группируем по упражнению
                from collections import defaultdict as _dd
                by_ex = _dd(list)
                for s in sets:
                    by_ex[s["exercise_name"]].append(s)
                lines = [f"📋 Текущая сессия #{workout_id}:"]
                for ex, ex_sets in by_ex.items():
                    parts = []
                    for s in ex_sets:
                        w = s.get("weight_kg")
                        r = s.get("reps")
                        n = s.get("notes") or ""
                        if w:
                            parts.append(f"{w}×{r}" if r else f"{w}кг")
                        elif r:
                            parts.append(f"{r} повт.")
                        elif n:
                            parts.append(n[:20])
                    lines.append(f"  • {ex}: {', '.join(parts)}")
                lines.append(f"\nПодходов: {len(sets)}")
                return "\n".join(lines)
        return "📋 Активная сессия идёт, но данных ещё нет."

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


async def _edit_plan(
    telegram_user_id: str | None,
    text: str,
    parsed: dict,
    prev_context: dict | None = None,
) -> str:
    from app.db import (
        get_best_planned_workout_for_edit,
        add_exercise_to_planned_workout,
        remove_exercise_from_planned_workout,
        replace_exercise_in_planned_workout,
    )
    target = parsed.get("target") or {}
    exercise_name = target.get("exercise_name")
    target_date = parsed.get("date")

    # Если парсер не извлёк дату — fallback на последнюю обсуждаемую тренировку
    if not target_date and prev_context:
        ctx_date = prev_context.get("current_workout_date")
        if ctx_date:
            target_date = str(ctx_date)[:10]

    workout = await get_best_planned_workout_for_edit(telegram_user_id, target_date=target_date)
    if not workout:
        if target_date:
            return f"Не нашёл активную плановую тренировку на {target_date}. Создай её или уточни дату."
        return "Не нашёл активный план для редактирования. Сначала создай план или уточни дату."

    workout_id = workout.get("id")
    summary = parsed.get("summary") or ""

    # Список упражнений в плане — для fuzzy match
    existing_names = []
    for ex in (workout.get("exercises") or []):
        nm = ex.get("exercise_name")
        if nm:
            existing_names.append(nm)

    edit_prompt = f"""
Пользователь хочет изменить план тренировки (ID {workout_id}).

Запрос: {text}
Существующие упражнения в плане:
{chr(10).join(f"- {n}" for n in existing_names) if existing_names else "(нет)"}

Полный план: {json.dumps(workout, ensure_ascii=False, default=str)[:2000]}

ПРАВИЛА:
- exercise_name ОБЯЗАТЕЛЬНО ТОЧНОЕ совпадение из списка ВЫШЕ (буква в букву).
- Пользователь может назвать неточно — найди ближайшее и используй ПОЛНОЕ название из списка.
- Если упражнения нет в списке и operation=remove/replace/update → operation="unknown".
- Для add — новое название.

DIFF operation vs update vs replace:
- "поменяй жим на тягу" / "замени жим на тягу" / "вместо жима — тяга" → REPLACE.
  exercise_name="<точное имя жима в плане>", new_exercise_name="тяга".
- "замени брусья на отжимания 3×15" → REPLACE.
  exercise_name="Брусья", new_exercise_name="Отжимания", sets=3, reps_min=15.
- "увеличь вес жима до 90" / "сделай 4 по 8 в жиме" / "поставь 90 кг в жиме" → UPDATE.
  Только параметры меняются, упражнение остаётся.
- "замени жим на тягу 4 по 10 80 кг" → REPLACE с параметрами (заполни sets/reps/weight).
- "добавь жим в план" — operation=add, exercise_name="жим" (НОВОЕ упражнение).
- ВАЖНО: если в запросе есть фразы "замени X на Y" / "поменяй X на Y" / "вместо X сделай Y" —
  это всегда REPLACE, никогда не ADD.

Парсинг "4 по 10" / "4 на 10" / "4×10": sets=4, reps_min=10 (НЕ reps_text).
"3 по 8-10" → sets=3, reps_min=8, reps_max=10.
"4 подхода 80 кг 10 раз" → sets=4, weight_kg=80, reps_min=10.
"без веса" / "своим весом" → weight_kg=0 (не null).

Верни JSON:
{{
  "operation": "add | remove | replace | update | unknown",
  "exercise_name": "<точное из списка>",
  "new_exercise_name": "<для replace>",
  "sets": null, "reps_min": null, "reps_max": null, "weight_kg": null,
  "notes": null
}}
Только JSON.
"""
    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
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
            new_sets=edit_parsed.get("sets"),
            new_reps_min=edit_parsed.get("reps_min"),
            new_reps_max=edit_parsed.get("reps_max"),
            new_weight_kg=edit_parsed.get("weight_kg"),
            reset_params=True,
        )
        if result.get("ok"):
            lines = [f"Заменил «{ex_name}» на «{new_name}»."]
            if result.get("applied_params"):
                p = result["applied_params"]
                detail = []
                if p.get("sets") and (p.get("reps_min") or p.get("reps_max")):
                    rep = p.get("reps_min")
                    if p.get("reps_max") and p["reps_max"] != p["reps_min"]:
                        rep = f"{p['reps_min']}-{p['reps_max']}"
                    detail.append(f"{p['sets']}×{rep}")
                if p.get("weight_kg"):
                    detail.append(f"{p['weight_kg']} кг")
                if detail:
                    lines.append(f"Параметры: {', '.join(detail)}")
            if result.get("asked_for_params"):
                lines.append("")
                lines.append("⚠️ Параметры (подходы, повторы, вес, заметки) сброшены — старые от другого упражнения.")
                lines.append("Скажи новые: например «4×10 80 кг» или «3 по 8-10, без веса».")
            return "\n".join(lines)
        return result.get("message") or f"Упражнение «{ex_name}» не найдено."

    if operation == "update" and ex_name:
        from app.db.engine import get_session
        from sqlalchemy import text as sql_text
        sets_v = edit_parsed.get("sets")
        rmin = edit_parsed.get("reps_min")
        rmax = edit_parsed.get("reps_max")
        w = edit_parsed.get("weight_kg")
        if all(v is None for v in (sets_v, rmin, rmax, w)):
            return "Не понял что менять. Скажи: «вес 80», «4 по 10», «3×8-10»."

        # build SET clause
        sets_parts = []
        params = {"wid": workout_id, "pat": f"%{ex_name}%"}
        if sets_v is not None:
            sets_parts.append("target_sets = :s")
            params["s"] = int(sets_v)
        if rmin is not None:
            sets_parts.append("target_reps_min = :rmin")
            params["rmin"] = int(rmin)
        if rmax is not None:
            sets_parts.append("target_reps_max = :rmax")
            params["rmax"] = int(rmax)
        if w is not None:
            sets_parts.append("target_weight_kg = :w")
            params["w"] = float(w)
        # if numeric provided, also clear reps_text
        if rmin is not None or rmax is not None:
            sets_parts.append("target_reps_text = NULL")

        async with get_session() as s:
            res = await s.execute(sql_text(f"""
                UPDATE planned_exercises
                SET {', '.join(sets_parts)}
                WHERE planned_workout_id = :wid AND lower(exercise_name) LIKE lower(:pat)
                RETURNING exercise_name, target_sets, target_reps_min, target_reps_max, target_weight_kg
            """), params)
            rows = res.mappings().all()
            await s.commit()
        if not rows:
            return f"Не нашёл «{ex_name}» в плане."
        row = rows[0]
        return (
            f"✏️ Обновил «{row['exercise_name']}»: "
            f"{row.get('target_sets') or '-'}×{row.get('target_reps_min') or '-'}"
            + (f"-{row.get('target_reps_max')}" if row.get('target_reps_max') and row.get('target_reps_max') != row.get('target_reps_min') else "")
            + (f", {format_number(row.get('target_weight_kg'))} кг" if row.get('target_weight_kg') is not None else "")
        )

    return f"Уточни что изменить: добавить, убрать, заменить или обновить параметры упражнения?\n\nТекущий план: {workout.get('title', 'без названия')}"


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


async def handle_fitness_action_v2(telegram_user_id: str | None, text: str) -> "str | BotReply | None":
    """Public entry. Wraps the inner handler with self-learning hooks + conversation context."""

    forget_reply = await handle_forget_request(telegram_user_id, text)
    if forget_reply:
        return forget_reply

    correction_reply = await handle_self_correction(telegram_user_id, text)
    if correction_reply:
        return correction_reply

    # Load previous conversation context (what date/workout we were just discussing)
    prev = None
    try:
        prev = await get_last_interaction(telegram_user_id) if telegram_user_id else None
    except Exception:
        prev = None

    # ── Dormant session auto-close ────────────────────────────────────────
    dormant_notice = ""
    t_low = text.lower()
    is_resume_phrase = any(p in t_low for p in [
        "продолжаю прерван", "продолжаю вчерашн", "возобнов", "оживи",
        "продолжаю #", "resume",
    ])
    if not is_resume_phrase:
        try:
            pending_pre = await get_latest_fitness_pending_decision(telegram_user_id)
            sess_pre = _active_session_context_from_pending(pending_pre)
            if _is_dormant(sess_pre):
                age = sess_pre.get("_age_hours")
                old_wid = sess_pre.get("workout_id")
                if pending_pre and pending_pre.get("id"):
                    await resolve_fitness_pending_decision(pending_pre["id"], status="resolved")
                dormant_notice = (
                    f"ℹ️ Прошлая сессия #{old_wid} ({age:.1f}ч назад) автоматически закрыта. "
                    f"Если хотел продолжить — скажи «продолжаю прерванную».\n\n"
                )
        except Exception:
            pass

    response = await _handle_fitness_action_v2_inner(telegram_user_id, text, prev_context=prev)
    if dormant_notice:
        if isinstance(response, str):
            response = dormant_notice + response
        elif isinstance(response, BotReply):
            response = BotReply(text=dormant_notice + response.text, keyboard=response.keyboard)

    if response and telegram_user_id:
        try:
            text_for_log = response.text if isinstance(response, BotReply) else response
            await save_last_interaction(
                telegram_user_id=telegram_user_id,
                input_text=text,
                bot_response=text_for_log,
            )
        except Exception:
            pass

    return response


async def _handle_fitness_action_v2_inner(
    telegram_user_id: str | None,
    text: str,
    prev_context: dict | None = None,
) -> "str | BotReply | None":
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    active_session = await _validate_active_session_or_resolve(telegram_user_id, pending)
    _PREV = prev_context or {}

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
    # bypass the active_session check entirely — НО ТОЛЬКО ЕСЛИ НЕТ АКТИВНОЙ СЕССИИ.
    # Иначе "Подтягивания 10 раз" в сессии создаст новый план.
    plan_like = is_monthly or is_weekly or is_complex or (is_long and has_plan_intent)
    if plan_like and active_session and active_session.get("workout_id"):
        # Активная сессия — приоритет. Не считаем сообщение планом.
        plan_like = False
    if plan_like:
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
        parsed_multi = await parse_fitness_action_v2(text, active_session=active_session, telegram_user_id=telegram_user_id, prev_context=_PREV)
        if parsed_multi.get("action") == "log_workout_sets" and parsed_multi.get("logged_exercises"):
            # Disambiguation: спрашиваем юзера лог vs план если нет явных глагольных маркеров
            disamb = await _maybe_ask_log_or_plan(telegram_user_id, text, parsed_multi)
            if disamb is not None:
                return disamb
            return await _log_workout_sets(telegram_user_id, text, parsed_multi, active_session)

    # ── HARD-route на "начинаю тренировку" → создаём пустую активную сессию ──
    start_session_triggers = [
        "начинаю делать сегодняшнюю", "начинаю делать тренировку",
        "стартую тренировку", "стартую сессию", "приступаю к тренировке",
        "запускаю активную сессию", "начинаю тренировку сегодня",
        "начинаю тренировку", "начал тренировку", "приступаю",
        "я делаю тренировку", "я начинаю тренировку",
    ]
    # Старт сессии срабатывает если нет активной СЕГОДНЯ. Если есть сессия от
    # вчера/раньше — её закрываем и стартуем новую.
    today = date.today().isoformat()
    _session_is_for_today = (
        active_session
        and str(active_session.get("workout_date") or "")[:10] == today
        and not active_session.get("_dormant")
    )
    if any(p in t_lower for p in start_session_triggers) and not _session_is_for_today:
        today = date.today().isoformat()
        # Если был выбран конкретный плановый день — стартуем сессию для НЕГО,
        # не для сегодня. Это позволяет логировать пропущенные/будущие дни вручную.
        selected_date = None
        try:
            from app.modules.fitness.planned_workout_executor import _get_selected_planned_workout_context
            from app.db import get_planned_workout_by_id
            sel_ctx = await _get_selected_planned_workout_context(telegram_user_id)
            if sel_ctx and sel_ctx.get("planned_workout_id"):
                sel_data = await get_planned_workout_by_id(int(sel_ctx["planned_workout_id"]))
                if sel_data and sel_data.get("workout"):
                    pd = sel_data["workout"].get("planned_date")
                    if hasattr(pd, "isoformat"):
                        pd = pd.isoformat()
                    if pd:
                        selected_date = str(pd)[:10]
        except Exception:
            selected_date = None

        target_date = selected_date or today
        # Найти плановую тренировку на target_date
        planned = await get_today_planned_workout(telegram_user_id, target_date)
        focus = None
        focus_label = None
        if planned:
            w = planned.get("workout") or {}
            focus = w.get("focus")
            focus_label = w.get("focus_label")
        # Заменяем today на target_date для остальной логики этого блока
        today = target_date

        # Создать пустую active_session workout
        workout_id = await save_fitness_workout_session_v2(
            telegram_user_id=telegram_user_id,
            workout_date=today,
            workout_type="actual",
            focus=focus,
            focus_label=focus_label,
            source_text=text,
            notes=None,
            exercises=[],
        )

        session_context = {
            "workout_id": workout_id,
            "workout_date": today,
            "current_exercise": None,
            "session_status": "active",
            "started_at": _now_iso(),
            "last_activity_at": _now_iso(),
            "last_training_activity_at": _now_iso(),
            "last_action": "session_started",
        }
        await create_fitness_pending_decision(
            telegram_user_id=telegram_user_id,
            decision_type="active_workout_session",
            context=session_context,
            source_text=text,
        )

        start_msg = (
            f"▶️ Активная сессия #{workout_id} запущена.\n"
            f"Дата: {format_human_date(today)}"
            + (f"\nФокус: {focus_label}" if focus_label else "")
        )

        # Если в том же сообщении есть упоминание подхода — парсим и пишем.
        # Триггеры подхода: есть число+(кг|повтор|раз) или числовой паттерн NxM.
        _has_kg_or_reps_inline = bool(re.search(r"\d+\s*(кг|килограмм|повтор|раз)", t_lower)) or bool(re.search(r"\d+\s*[×x✕х]\s*\d+", text))
        if _has_kg_or_reps_inline:
            # Активная сессия теперь существует — перезапрашиваем для прокидки
            pending_after = await get_latest_fitness_pending_decision(telegram_user_id)
            active_after = _active_session_context_from_pending(pending_after)
            parsed_set = await parse_fitness_action_v2(
                text, active_session=active_after,
                telegram_user_id=telegram_user_id, prev_context=_PREV,
            )
            if parsed_set.get("logged_exercises"):
                log_result = await _log_workout_sets(
                    telegram_user_id, text, parsed_set, active_after,
                )
                # Объединяем стартовое сообщение и лог в один ответ
                log_text = log_result.text if isinstance(log_result, BotReply) else log_result
                return f"{start_msg}\n\n{log_text}"

        return (
            start_msg + "\n\n"
            "Диктуй подходы. Пример: «жим штанги 80 на 10», «следующий 80×8», "
            "«последний 75×10, перехожу к гантелям»."
        )

    # ── HARD-route на запись факта: "сделал/выполнил X N кг M раз" ──
    # Эти фразы ВСЕГДА факт, никогда не правка плана.
    fact_verbs = re.search(
        r"\b(сделал[аои]?|сделанн|выполнил[аио]?|записывай|запиши|залогируй|первый подход|второй подход|третий подход|последний подход|был[оаи]? подход|рабочий подход)\b",
        t_lower,
    )
    has_kg_or_reps = bool(re.search(r"\d+\s*(кг|килограмм|повтор|раз)", t_lower)) or has_sets_pattern
    looks_like_fact = bool(fact_verbs) and has_kg_or_reps

    if looks_like_fact:
        parsed_fact = await parse_fitness_action_v2(text, active_session=active_session, telegram_user_id=telegram_user_id, prev_context=_PREV)
        # Force into log path even if parser thought it was edit_plan
        if parsed_fact.get("action") in ("log_workout_sets", "edit_plan", "continue_current_exercise"):
            # Try to extract logged_exercises; if edit_plan was chosen, coerce
            if parsed_fact.get("logged_exercises"):
                return await _log_workout_sets(telegram_user_id, text, parsed_fact, active_session)
            # Synthesize a logged_exercises from parser's edit_plan output
            tgt = parsed_fact.get("target") or {}
            ex_name = tgt.get("exercise_name")
            if ex_name:
                # rough fallback: parse numbers from text
                nums = re.findall(r"\d+(?:[.,]\d+)?", text)
                weight = None
                reps = None
                if nums:
                    vals = [float(n.replace(",", ".")) for n in nums]
                    # heuristic: first >20 = weight, smaller = reps
                    big = [v for v in vals if v >= 20]
                    small = [v for v in vals if v < 30]
                    if big:
                        weight = big[0]
                    if small:
                        reps = int(small[-1])
                synth = {
                    "action": "log_workout_sets",
                    "date": date.today().isoformat(),
                    "workout": {},
                    "logged_exercises": [{
                        "exercise_name": ex_name,
                        "sets": [{"set_number": 1, "weight_kg": weight, "reps": reps}],
                    }],
                }
                return await _log_workout_sets(telegram_user_id, text, synth, active_session)

    # ── FAST-PATH: архивные фразы. Прямой dispatch без AI-парсера. ─────────
    # "Что я делал сегодня/вчера" / "вчерашняя/прошлая/последняя тренировка"
    # — частые запросы, не нужно гонять Sonnet парсер. Сразу вызываем БД.
    _t_arch = (text or "").lower().replace("ё", "е").strip()
    _archive_today = any(p in _t_arch for p in [
        "я делал сегодня", "я сделал сегодня", "я записал сегодня",
        "что я делал сегодня", "что я сделал сегодня",
    ])
    _archive_yesterday = any(p in _t_arch for p in [
        "я делал вчера", "я сделал вчера", "я записал вчера",
        "что я делал вчера", "что я сделал вчера",
        "вчерашняя трен", "вчерашнюю трен",
    ])
    _archive_week = any(p in _t_arch for p in [
        "я делал на этой неделе", "я сделал на этой неделе",
        "что я делал на неделе", "что на этой неделе сделал",
        "что я делал за неделю", "что я сделал за неделю",
    ])
    _archive_last = any(p in _t_arch for p in [
        "прошлая тренир", "прошлую тренир", "последняя тренир", "последнюю тренир",
        "покажи прошлую", "покажи последнюю",
    ])
    if _archive_today or _archive_yesterday or _archive_week or _archive_last:
        from datetime import timedelta as _td
        if _archive_today:
            tgt = date.today().isoformat()
            workouts = await get_completed_workouts_in_period(telegram_user_id, tgt, tgt)
            return format_completed_period(workouts, f"Тренировки за {format_human_date(tgt)}")
        if _archive_yesterday:
            tgt = (date.today() - _td(days=1)).isoformat()
            workouts = await get_completed_workouts_in_period(telegram_user_id, tgt, tgt)
            return format_completed_period(workouts, f"Тренировки за {format_human_date(tgt)}")
        if _archive_week:
            start, end = week_bounds()
            workouts = await get_completed_workouts_in_period(telegram_user_id, start, end)
            return format_completed_period(workouts, f"Тренировки за неделю ({format_human_date(start, include_weekday=False)} — {format_human_date(end, include_weekday=False)})")
        if _archive_last:
            last = await get_last_workout(telegram_user_id)
            return format_last_workout(last)

    # ── FAST-PATH: показ текущей сессии ────────────────────────────────────
    # "Покажи сессию", "Что я уже записал", "Текущие подходы" и т.п.
    # Это нужно обработать ДО router_hardening, потому что active_session уже есть.
    if active_session and active_session.get("workout_id"):
        _show_session_phrases = [
            "покажи сессию", "покажи текущую", "покажи что записал",
            "что уже записал", "что я записал", "что в сессии",
            "сколько записал", "что у меня в сессии", "покажи тренировку",
            "что записано", "итог сессии", "текущие подходы",
            "покажи текущую сессию", "покажи мне сессию",
        ]
        if any(p in t_lower for p in _show_session_phrases):
            session_display = await _format_active_session_contents(telegram_user_id, active_session)
            return session_display

    # ── FAST-PATH B2: "Поставь на [день]" → add_custom_workout ─────────────
    # Обходим router_hardening который перехватывает "поставь на..." через
    # parse_workout_edit_action и возвращает "плановая тренировка не найдена".
    _is_postavь = bool(re.search(
        r"\bпостав[ьлю]\s+на\s+"
        r"(сегодня|завтра|послезавтра"
        r"|пн|вт|ср|чт|пт|сб|вс"
        r"|понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскрес\w*)\b",
        t_lower,
    ))
    if _is_postavь:
        parsed_b2 = await parse_fitness_action_v2(
            text, active_session=active_session,
            telegram_user_id=telegram_user_id, prev_context=_PREV,
        )
        parsed_b2["action"] = "add_custom_workout"
        parsed_b2.setdefault("confidence", 0.9)
        return await _add_custom_workout(telegram_user_id, text, parsed_b2)

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

    parsed = await parse_fitness_action_v2(text, active_session=active_session, telegram_user_id=telegram_user_id, prev_context=_PREV)

    action = parsed.get("action")
    confidence = float(parsed.get("confidence") or 0)

    # HARD-OVERRIDE: фразы с "я делал/сделал/записал" в прошлом времени всегда архив
    t_lc = text.lower().replace("ё", "е")
    archive_phrases = [
        ("я делал сегодня", "show_completed_day", "today"),
        ("я делал вчера", "show_completed_day", "yesterday"),
        ("я делал позавчера", "show_completed_day", None),
        ("я сделал сегодня", "show_completed_day", "today"),
        ("я сделал вчера", "show_completed_day", "yesterday"),
        ("я записал сегодня", "show_completed_day", "today"),
        ("я записал вчера", "show_completed_day", "yesterday"),
        ("я делал на этой неделе", "show_completed_week", None),
        ("я сделал на этой неделе", "show_completed_week", None),
        ("я делал в этом месяце", "show_completed_month", None),
        ("я сделал в этом месяце", "show_completed_month", None),
    ]
    for phrase, override_action, day_hint in archive_phrases:
        if phrase in t_lc:
            action = override_action
            if day_hint == "today":
                parsed["date"] = date.today().isoformat()
            elif day_hint == "yesterday":
                from datetime import timedelta as _td
                parsed["date"] = (date.today() - _td(days=1)).isoformat()
            confidence = 0.95
            break

    # HARD-OVERRIDE: "Поставь на [день] [фокус]: [упражнения]" — создание нового плана,
    # никогда не правка существующего. AI путает "поставь" с edit_plan.
    if action in ("edit_plan", "move_workout", "replace_today_workout") and re.search(
        r"\bпостав[ьлю]\s+на\s+"
        r"(сегодня|завтра|послезавтра"
        r"|пн|вт|ср|чт|пт|сб|вс"
        r"|понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскрес\w*)\b",
        t_lc,
    ):
        action = "add_custom_workout"
        parsed["action"] = "add_custom_workout"
        confidence = 0.9

    if not action or action in ("unknown", "clarify") or confidence < 0.55:
        # Если текст явно НЕ про фитнес и нет активной сессии — отвечаем как общий AI
        if not active_session and not _looks_like_fitness_text(text):
            from app.ai import generate_general_answer
            return await generate_general_answer(text)
        return await _ask_clarification(text)

    if action == "show_today_workout":
        today_iso = date.today().isoformat()
        data = await get_today_planned_workout(telegram_user_id, today_iso)
        if not data:
            return "На сегодня активная плановая тренировка не найдена."
        await _remember_workout_context(telegram_user_id, today_iso, data)
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
        await _remember_workout_context(telegram_user_id, target_date, data)
        return f"Тренировка на {format_human_date(target_date)}:\n\n" + format_planned_workout(data)

    if action == "replace_today_workout":
        return await _create_or_replace_today_workout(telegram_user_id, text, parsed)

    # ── DISAMBIGUATION: лог vs план ────────────────────────────────────────
    # Если парсер выбрал между add_custom_workout/log_workout_sets без
    # явных глагольных маркеров — спрашиваем пользователя кнопками.
    if action in ("add_custom_workout", "log_workout_sets"):
        # Если активная сессия — всегда лог
        if not (active_session and active_session.get("workout_id")):
            disamb_reply = await _maybe_ask_log_or_plan(telegram_user_id, text, parsed)
            if disamb_reply is not None:
                return disamb_reply

    if action == "add_custom_workout":
        # Если есть активная сессия СЕГОДНЯ — это запись подходов, не план.
        # "Подтягивания 10 раз" / "Брусья 3×10" / "Планка 60 секунд" в сессии = log.
        if active_session and active_session.get("workout_id"):
            existing_date = str(active_session.get("workout_date") or "")[:10]
            if existing_date == date.today().isoformat():
                # Конвертируем workout.exercises → logged_exercises (set каждого упр = 1)
                wk = parsed.get("workout") or {}
                exes = wk.get("exercises") or []
                if exes:
                    logged = []
                    for ex in exes:
                        sets_count = ex.get("target_sets") or 1
                        reps = ex.get("target_reps_min") or ex.get("target_reps_max") or None
                        weight = ex.get("target_weight_kg")
                        synthetic_sets = []
                        for i in range(sets_count):
                            synthetic_sets.append({
                                "set_number": i + 1,
                                "weight_kg": weight,
                                "reps": reps,
                            })
                        logged.append({
                            "exercise_name": ex.get("exercise_name"),
                            "sets": synthetic_sets,
                        })
                    synth = {
                        "action": "log_workout_sets",
                        "date": date.today().isoformat(),
                        "workout": {},
                        "logged_exercises": logged,
                    }
                    return await _log_workout_sets(telegram_user_id, text, synth, active_session)
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
        return await _edit_plan(telegram_user_id, text, parsed, prev_context=_PREV)

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

    # show_personal_records отключено
    # show_exercise_stats отключено

    if action == "finish_workout":
        if active_session and active_session.get("workout_id"):
            wid = int(active_session["workout_id"])
            return await _finish_workout_with_summary(telegram_user_id, wid)
        return "Активная тренировочная сессия не найдена. Возможно, она уже завершена."

    if action == "record_measurement":
        parsed["_source_text"] = text
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

    # compare_weeks отключено
    if action == "quick_stats":
        return await _quick_stats(telegram_user_id)

    if action == "show_current_session":
        if active_session and active_session.get("workout_id"):
            return await _format_active_session_contents(telegram_user_id, active_session)
        return "Активная сессия не найдена."

    if action == "edit_last_set":
        return await _edit_last_set(telegram_user_id, parsed, active_session, text=text)

    # Травмы и ограничения отключены (add_constraint / list_constraints / resolve_constraint)

    # ─── Пропуски/сдвиги/очистка плана ──────────────────────────────────
    if action == "skip_workout":
        target = parsed.get("target") or {}
        d = target.get("from_date") or parsed.get("date")
        if not d:
            from datetime import timedelta
            d = (date.today() - timedelta(days=1)).isoformat()
        n = await skip_planned_workout(telegram_user_id, d, reason=text[:200])
        if n == 0:
            return f"На {format_human_date(d)} плановых тренировок не было (или уже не активны)."
        return f"📋 Отметил {n} тренировку(и) на {format_human_date(d)} как пропущенную."

    if action == "shift_plan":
        target = parsed.get("target") or {}
        from_d = target.get("from_date") or date.today().isoformat()
        days = int(target.get("shift_days") or 1)
        n = await shift_planned_workouts(telegram_user_id, from_d, days)
        return f"📅 Сдвинул {n} тренировок с {format_human_date(from_d)} на {days} дней вперёд."

    if action == "clear_plan_period":
        period = parsed.get("period") or {}
        s = period.get("start_date") or date.today().isoformat()
        e = period.get("end_date") or s
        n = await cancel_plan_period(telegram_user_id, s, e)
        return f"🧹 Отменил {n} плановых тренировок с {format_human_date(s)} по {format_human_date(e)}."

    if action == "merge_workouts":
        return await _merge_workouts(telegram_user_id, parsed)

    # ─── Откат записи ───────────────────────────────────────────────────
    if action == "delete_last_n_sets_action":
        if not active_session or not active_session.get("workout_id"):
            return "Активной сессии нет — нечего откатывать."
        n = int((parsed.get("target") or {}).get("n_sets_to_delete") or 1)
        deleted = await delete_last_n_sets(int(active_session["workout_id"]), n)
        return f"↩️ Удалил последние {deleted} подход(ов)."

    if action == "delete_last_exercise":
        wid = (active_session or {}).get("workout_id")
        if not wid:
            last = await get_last_workout(telegram_user_id)
            if not last:
                return "Нет тренировок для отката."
            wid = last["workout"]["id"]
        name, n = await delete_last_exercise_from_workout(int(wid))
        if not name:
            return "Не нашёл последнего упражнения для удаления."
        return f"↩️ Удалил упражнение «{name}» полностью ({n} подходов)."

    if action == "delete_workout_action":
        target = parsed.get("target") or {}
        target_date = parsed.get("date") or target.get("from_date")
        wid = (active_session or {}).get("workout_id") if not target_date else None
        if not wid:
            last = await get_last_workout(telegram_user_id)
            if last:
                wid = last["workout"]["id"]
        if not wid:
            return "Не нашёл тренировку для удаления."

        # Confirmation prompt with inline buttons (destructive)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Удалить", callback_data=f"fit:confirm_delete_workout:{wid}"),
            InlineKeyboardButton("✖ Отмена", callback_data="fit:cancel:_"),
        ]])
        return BotReply(
            text=f"⚠️ Точно удалить тренировку #{wid}? Это снесёт все её подходы.",
            keyboard=kb,
        )

    if action == "rename_last_exercise":
        target = parsed.get("target") or {}
        new_name = target.get("new_exercise_name")
        old_name = target.get("exercise_name")
        if not new_name:
            return "Уточни на что переименовать: «переименуй последнее на жим ногами»."
        wid = (active_session or {}).get("workout_id")
        if not wid:
            last = await get_last_workout(telegram_user_id)
            if not last:
                return "Нет тренировок для переименования."
            wid = last["workout"]["id"]
        if not old_name:
            # take the most recent exercise name
            last = await get_last_workout(telegram_user_id)
            sets = (last or {}).get("sets") or []
            if sets:
                old_name = sets[-1].get("exercise_name")
        if not old_name:
            return "Не нашёл последнего упражнения."
        n = await rename_exercise_in_workout(int(wid), old_name, new_name)
        return f"✏️ Переименовал «{old_name}» → «{new_name}» в {n} подходах." if n else "Не нашёл подходов."

    if action == "mark_last_as_warmup":
        if not active_session or not active_session.get("workout_id"):
            return "Активной сессии нет."
        from app.db.engine import get_session
        from sqlalchemy import text as sql_text
        target = parsed.get("target") or {}
        sn = target.get("set_number")
        async with get_session() as s:
            if sn:
                await s.execute(sql_text("""
                    UPDATE fitness_exercise_sets
                    SET notes = COALESCE(notes, '') || ' | разминка'
                    WHERE workout_id = :wid AND set_number = :sn
                """), {"wid": int(active_session["workout_id"]), "sn": int(sn)})
            else:
                await s.execute(sql_text("""
                    UPDATE fitness_exercise_sets
                    SET notes = COALESCE(notes, '') || ' | разминка'
                    WHERE id = (
                        SELECT id FROM fitness_exercise_sets
                        WHERE workout_id = :wid ORDER BY id DESC LIMIT 1
                    )
                """), {"wid": int(active_session["workout_id"])})
            await s.commit()
        return "🔥 Отметил как разминочный."

    if action == "export_csv":
        return await _export_csv(telegram_user_id, parsed)

    # ─── Качество жизни (UX) ─────────────────────────────────────────────
    if action == "show_last_recorded":
        last = await get_last_interaction(telegram_user_id)
        if not last or not last.get("bot_response"):
            return "Не помню что записал в последний раз."
        prev = last.get("bot_response") or ""
        return f"📋 Мой последний ответ:\n\n{prev}"

    if action == "undo_last":
        return await _undo_last_action(telegram_user_id)

    if action == "add_set_note":
        return await _add_set_note(telegram_user_id, parsed, active_session)

    if action == "resume_session":
        if active_session and active_session.get("workout_id"):
            pending = await get_latest_fitness_pending_decision(telegram_user_id)
            if pending:
                ctx = pending.get("context_json") or {}
                ctx["session_status"] = "active"
                ctx["last_activity_at"] = _now_iso()
                ctx["last_training_activity_at"] = _now_iso()
                ctx.pop("_dormant", None)
                await update_fitness_pending_decision_context(pending["id"], ctx)
            return f"▶️ Возобновил сессию #{active_session['workout_id']}. Продолжай диктовать подходы."
        # Try to revive the latest workout
        last = await get_last_workout(telegram_user_id)
        if not last:
            return "Не нашёл прошлой тренировки для возобновления."
        wd = str(last["workout"].get("workout_date"))[:10]
        return f"Последняя тренировка: {format_human_date(wd)} (ID #{last['workout']['id']}). Активной сессии нет — диктуй новые подходы, они уйдут в новую."

    if action == "tag_feeling":
        return await _tag_feeling(telegram_user_id, text, parsed, active_session)

    # Удалены: show_volume_by_group, show_lagging_group, show_trend, show_1rm,
    # find_workout_by_exercise, log_pain_action, sick_leave, show_plateau —
    # не нужны по требованию пользователя.

    if action == "show_streak":
        return await _show_streak(telegram_user_id)

    # ═══ Пакет 4: Шаблоны и программирование ═══
    if action == "save_template":
        return await _save_template(telegram_user_id, parsed, active_session)

    if action == "apply_template":
        return await _apply_template(telegram_user_id, parsed)

    if action == "list_templates":
        return await _list_templates_handler(telegram_user_id)

    if action == "bulk_edit_exercises":
        return await _bulk_edit_exercises(telegram_user_id, parsed)

    if action == "set_goal":
        return await _set_goal(telegram_user_id, parsed, text)

    if action == "show_goals":
        return await _show_goals(telegram_user_id)

    # ═══ Пакет 5: Отчёты ═══
    if action == "coach_report":
        return await _coach_report(telegram_user_id, parsed)

    if action == "weekly_summary":
        return await _weekly_summary(telegram_user_id)

    # ═══ Копирование периодов ═══
    if action == "copy_week_to_next":
        cur_s, cur_e = week_bounds()
        from datetime import timedelta
        dst_s = (datetime.strptime(cur_s, "%Y-%m-%d").date() + timedelta(days=7)).isoformat()
        result = await copy_planned_period(
            telegram_user_id=telegram_user_id,
            src_start=cur_s, src_end=cur_e,
            dst_start=dst_s, skip_existing=True,
        )
        if result["copied"] == 0:
            return f"⚠️ Не нашёл что копировать в текущей неделе ({format_human_date(cur_s)} — {format_human_date(cur_e)}). Пропущено (уже есть): {result['skipped']}."
        return (
            f"📋 Скопировал {result['copied']} тренировок с {format_human_date(cur_s)} — {format_human_date(cur_e)} "
            f"на {format_human_date(result['dst_first'])} — {format_human_date(result['dst_last'])}."
            + (f"\nПропущено (уже было запланировано): {result['skipped']}." if result['skipped'] else "")
        )

    # ─── Напоминания ───────────────────────────────────────────────
    if action == "schedule_reminder_action":
        return await _schedule_reminder_action(telegram_user_id, parsed, text)

    if action == "list_reminders":
        rems = await list_pending_reminders(telegram_user_id, limit=20)
        if not rems:
            return "Активных напоминаний нет."
        lines = [f"⏰ Активные напоминания ({len(rems)}):", ""]
        for r in rems:
            payload = r.get("payload_json") or {}
            if isinstance(payload, str):
                import json as _j
                try:
                    payload = _j.loads(payload)
                except Exception:
                    payload = {}
            txt = payload.get("text") or r.get("kind") or "напоминание"
            fire = r.get("fire_at")
            fire_str = str(fire)[:16]
            rec = r.get("recurrence")
            rec_str = f" [{rec}]" if rec else ""
            lines.append(f"  #{r['id']} {fire_str}{rec_str}: {txt}")
        return "\n".join(lines)

    if action == "cancel_reminder_action":
        target = parsed.get("target") or {}
        rid = target.get("constraint_id") or target.get("set_number")
        if not rid:
            return "Уточни ID: «отмени напоминание #5»."
        await cancel_reminder(int(rid))
        return f"❌ Напоминание #{rid} отменено."

    if action == "show_measurements_trend":
        return await _show_measurements_trend(telegram_user_id, parsed)

    if action == "copy_period_to_period":
        period = parsed.get("period") or {}
        target = parsed.get("target") or {}
        src_s = period.get("start_date")
        src_e = period.get("end_date")
        dst_s = target.get("to_date")
        if not src_s or not src_e or not dst_s:
            return "Уточни: «скопируй с 18.05 по 24.05 начиная с 01.06»."
        result = await copy_planned_period(
            telegram_user_id=telegram_user_id,
            src_start=src_s, src_end=src_e,
            dst_start=dst_s, skip_existing=True,
        )
        if result["copied"] == 0:
            return f"⚠️ Нечего копировать с {format_human_date(src_s)} — {format_human_date(src_e)}."
        return (
            f"📋 Скопировал {result['copied']} тренировок с {format_human_date(src_s)} — {format_human_date(src_e)} "
            f"на {format_human_date(result['dst_first'])} — {format_human_date(result['dst_last'])}."
            + (f"\nПропущено: {result['skipped']}." if result['skipped'] else "")
        )

    if action == "non_fitness":
        # Делегируем общему AI-ответчику, не ломая активную сессию
        from app.ai import generate_general_answer
        return await generate_general_answer(text)

    # Никогда не возвращаем None молча — пусть юзер видит что бот не справился
    return (
        f"Я разобрал твой запрос как action='{action}' (confidence={confidence:.2f}), "
        f"но у меня нет обработчика для этого. Скажи проще или пришли /help."
    )


def _shift_date(d: date, days: int) -> date:
    from datetime import timedelta
    return d + timedelta(days=days)


async def _remember_workout_context(
    telegram_user_id: str | None,
    target_date: str | None,
    data: dict | None,
) -> None:
    """Запоминаем какую тренировку юзер только что обсудил — для разрешения 'её'/'оттуда'."""
    if not telegram_user_id or not data:
        return
    try:
        w = data.get("workout") or {}
        await save_last_interaction(
            telegram_user_id=telegram_user_id,
            input_text="",  # обновляется обёрткой
            bot_response="",
            current_workout_date=target_date,
            current_planned_workout_id=w.get("id"),
            current_focus=w.get("focus_label") or w.get("focus"),
        )
    except Exception:
        pass


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
    raw_date = parsed.get("date") or date.today().isoformat()
    # asyncpg хочет реальный date объект, не строку
    if isinstance(raw_date, str):
        try:
            measurement_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except Exception:
            measurement_date = date.today()
    else:
        measurement_date = raw_date

    if not any(v is not None for v in m.values() if not isinstance(v, str)):
        return "Не понял, какие замеры записать. Пример: «вес 80.5, талия 82, % жира 15»."

    # Дополнительно парсим из текста: "Голень N" и "Живот N" если AI парсер их пропустил
    source_text = parsed.get("_source_text") or ""
    if "calf_cm" not in m or m.get("calf_cm") is None:
        cm = re.search(r"\bголен[ьи]\s+(\d+(?:[.,]\d+)?)", source_text.lower())
        if cm:
            m["calf_cm"] = float(cm.group(1).replace(",", "."))
    if "belly_cm" not in m or m.get("belly_cm") is None:
        cm = re.search(r"\bживот\s+(\d+(?:[.,]\d+)?)", source_text.lower())
        if cm:
            m["belly_cm"] = float(cm.group(1).replace(",", "."))

    async with get_session() as session:
        result = await session.execute(sql_text("""
            INSERT INTO body_measurements
              (telegram_user_id, measurement_date, weight_kg, waist_cm, chest_cm,
               hips_cm, arm_cm, thigh_cm, neck_cm, calf_cm, belly_cm, notes)
            VALUES
              (:uid, :d, :w, :waist, :chest, :hips, :arm, :thigh, :neck, :calf, :belly, :notes)
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
            "calf": m.get("calf_cm"),
            "belly": m.get("belly_cm"),
            "notes": m.get("notes"),
        })
        mid = result.scalar()
        await session.commit()

    parts = [f"Записал замеры (ID: {mid}, {format_human_date(measurement_date)}):"]
    for key, label, unit in [
        ("weight_kg", "Вес", "кг"),
        ("neck_cm", "Шея", "см"),
        ("chest_cm", "Грудь", "см"),
        ("arm_cm", "Рука", "см"),
        ("belly_cm", "Живот", "см"),
        ("waist_cm", "Талия", "см"),
        ("hips_cm", "Бёдра", "см"),
        ("thigh_cm", "Бедро", "см"),
        ("calf_cm", "Голень", "см"),
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


async def _merge_workouts(telegram_user_id: str | None, parsed: dict) -> str:
    """Move planned exercises from one date into another."""
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    target = parsed.get("target") or {}
    from_d = target.get("from_date")
    to_d = target.get("merge_into_date") or target.get("to_date")
    if not from_d or not to_d:
        return "Уточни даты: «объедини тренировки 18.05 и 19.05 в 19.05»."

    async with get_session() as s:
        src = await s.execute(sql_text("""
            SELECT id FROM planned_workouts
            WHERE telegram_user_id = :uid AND planned_date = :d AND status = 'planned'
            ORDER BY id DESC LIMIT 1
        """), {"uid": telegram_user_id, "d": from_d})
        src_id = src.scalar()
        dst = await s.execute(sql_text("""
            SELECT id FROM planned_workouts
            WHERE telegram_user_id = :uid AND planned_date = :d AND status = 'planned'
            ORDER BY id DESC LIMIT 1
        """), {"uid": telegram_user_id, "d": to_d})
        dst_id = dst.scalar()

        if not src_id:
            return f"На {format_human_date(from_d)} плановой тренировки нет."
        if not dst_id:
            return f"На {format_human_date(to_d)} плановой тренировки нет — некуда сливать."

        max_order = await s.execute(sql_text("""
            SELECT COALESCE(MAX(exercise_order), 0) FROM planned_exercises
            WHERE planned_workout_id = :wid
        """), {"wid": dst_id})
        base = max_order.scalar() or 0

        moved = await s.execute(sql_text("""
            UPDATE planned_exercises
            SET planned_workout_id = :dst, exercise_order = exercise_order + :base
            WHERE planned_workout_id = :src
            RETURNING id
        """), {"dst": dst_id, "src": src_id, "base": base})
        n = len(moved.fetchall())

        await s.execute(sql_text("""
            UPDATE planned_workouts SET status = 'merged' WHERE id = :sid
        """), {"sid": src_id})
        await s.commit()

    return f"🔀 Объединил: перенёс {n} упражнений с {format_human_date(from_d)} в {format_human_date(to_d)}."


async def _export_csv(telegram_user_id: str | None, parsed: dict) -> str:
    """Build a CSV-formatted dump of completed workouts in [period] or all."""
    period = parsed.get("period") or {}
    s = period.get("start_date")
    e = period.get("end_date")
    fmt = ((parsed.get("target") or {}).get("export_format") or "csv").lower()

    if not s:
        from datetime import timedelta
        s = (date.today() - timedelta(days=30)).isoformat()
    if not e:
        e = date.today().isoformat()

    workouts = await get_completed_workouts_in_period(telegram_user_id, s, e)
    if not workouts:
        return f"За период {format_human_date(s)} — {format_human_date(e)} тренировок не записано."

    rows = []
    for w in workouts:
        for st in (w.get("sets") or []):
            rows.append({
                "date": str(w.get("workout_date"))[:10],
                "focus": w.get("focus_label") or w.get("focus") or "",
                "exercise": st.get("exercise_name") or "",
                "set": st.get("set_number") or "",
                "weight_kg": st.get("weight_kg") or "",
                "reps": st.get("reps") or "",
                "rpe": st.get("rpe") or "",
                "notes": (st.get("notes") or "").replace("\n", " ")[:200],
            })

    if fmt == "json":
        import json as _j
        body = _j.dumps(rows, ensure_ascii=False, indent=2)
        return f"```json\n{body[:3500]}\n```" if len(body) <= 3500 else f"⚠️ Данных много ({len(rows)} строк). Попроси более узкий период."

    if fmt == "txt":
        lines = [f"{r['date']} | {r['focus']} | {r['exercise']} | сет {r['set']} | {r['weight_kg']} кг × {r['reps']}" for r in rows]
        body = "\n".join(lines)
        return body if len(body) <= 3800 else body[:3800] + "\n... (обрезано)"

    # default CSV
    header = "date,focus,exercise,set,weight_kg,reps,rpe,notes"
    body_lines = [header]
    for r in rows:
        body_lines.append(",".join(
            f'"{str(r[k]).replace(chr(34), chr(39))}"'
            for k in ["date", "focus", "exercise", "set", "weight_kg", "reps", "rpe", "notes"]
        ))
    body = "\n".join(body_lines)
    if len(body) <= 3800:
        return f"```\n{body}\n```\n\n{len(rows)} строк, {len(workouts)} тренировок."

    # Big — send as document
    return BotReply(
        text=f"📎 Экспорт CSV: {len(rows)} подходов, {len(workouts)} тренировок за {s} — {e}",
        document_bytes=body.encode("utf-8"),
        document_filename=f"workouts_{s}_to_{e}.csv",
        document_caption=f"Тренировки {s} — {e}: {len(rows)} подходов в {len(workouts)} тренировках",
    )


# ═══════════════════════════════════════════════════════════════════════
# Пакет 2: Аналитика
# ═══════════════════════════════════════════════════════════════════════


async def _resolve_period(parsed: dict, default_days: int = 30) -> tuple[str, str]:
    """Resolve period from parsed.period.{start,end} or default to last N days."""
    from datetime import timedelta
    period = parsed.get("period") or {}
    s = period.get("start_date")
    e = period.get("end_date")
    if not s:
        s = (date.today() - timedelta(days=default_days - 1)).isoformat()
    if not e:
        e = date.today().isoformat()
    return s, e


async def _show_volume_by_group(telegram_user_id: str | None, parsed: dict) -> str:
    s, e = await _resolve_period(parsed, default_days=7)
    workouts = await get_completed_workouts_in_period(telegram_user_id, s, e)
    if not workouts:
        return f"За {format_human_date(s, include_weekday=False)} — {format_human_date(e, include_weekday=False)} тренировок нет."

    all_sets = []
    for w in workouts:
        for st in (w.get("sets") or []):
            all_sets.append(st)
    agg = aggregate_by_group(all_sets)

    sorted_groups = sorted(agg.items(), key=lambda x: -x[1]["tonnage"])
    lines = [f"📊 Объём по группам, {format_human_date(s, include_weekday=False)} — {format_human_date(e, include_weekday=False)}:", ""]
    for g, st in sorted_groups:
        tonnage = round(st["tonnage"], 0)
        lines.append(f"  {g}: {st['sets']} подх. · {st['exercises_count']} упр. · {format_number(tonnage)} кг")
    lines.append("")
    lines.append(f"Тренировок: {len(workouts)}")
    return "\n".join(lines)


async def _show_lagging_group(telegram_user_id: str | None, parsed: dict) -> str:
    """Сравниваем volume по группам за период; топ-3 минимума = отстающие."""
    s, e = await _resolve_period(parsed, default_days=30)
    workouts = await get_completed_workouts_in_period(telegram_user_id, s, e)
    if not workouts:
        return f"Нет тренировок за период {s} — {e} для анализа."

    all_sets = []
    for w in workouts:
        for st in (w.get("sets") or []):
            all_sets.append(st)
    agg = aggregate_by_group(all_sets)

    # Exclude кардио and "другое" from lagging analysis
    significant = {g: st for g, st in agg.items() if g not in ("кардио", "другое")}
    if not significant:
        return "Не нашёл силовых упражнений для анализа."

    sorted_low = sorted(significant.items(), key=lambda x: x[1]["sets"])[:3]
    total_sets = sum(st["sets"] for st in significant.values())

    lines = [f"📉 Что отстаёт (за {format_human_date(s, include_weekday=False)} — {format_human_date(e, include_weekday=False)}):", ""]
    for g, st in sorted_low:
        pct = round(st["sets"] / total_sets * 100, 1) if total_sets else 0
        lines.append(f"  {g}: {st['sets']} подходов ({pct}% от объёма) · {format_number(round(st['tonnage'], 0))} кг")
    lines.append("")
    lines.append("Рекомендация: добавь упражнения на отстающие группы в план.")
    return "\n".join(lines)


async def _show_streak(telegram_user_id: str | None) -> str:
    """Серия подряд активных дней + сводка за месяц."""
    from datetime import timedelta
    today = date.today()
    # Get unique workout dates in last 365 days
    workouts = await get_completed_workouts_in_period(
        telegram_user_id,
        (today - timedelta(days=365)).isoformat(),
        today.isoformat(),
    )
    if not workouts:
        return "Тренировок ещё не записано. Запиши первую — и начнём считать стрик."

    dates_set = set()
    for w in workouts:
        d = str(w.get("workout_date"))[:10]
        try:
            dates_set.add(datetime.strptime(d, "%Y-%m-%d").date())
        except Exception:
            pass

    # Current streak: count back from today
    streak = 0
    cur = today
    while cur in dates_set:
        streak += 1
        cur -= timedelta(days=1)

    # Allow "yesterday-only" partial streak
    if streak == 0 and (today - timedelta(days=1)) in dates_set:
        cur = today - timedelta(days=1)
        while cur in dates_set:
            streak += 1
            cur -= timedelta(days=1)
        streak_label = f"{streak} (с перерывом сегодня)"
    else:
        streak_label = str(streak)

    # Counts
    last_7 = sum(1 for d in dates_set if (today - d).days < 7)
    last_30 = sum(1 for d in dates_set if (today - d).days < 30)
    last_90 = sum(1 for d in dates_set if (today - d).days < 90)

    lines = [
        "🔥 Серия и частота:",
        "",
        f"  Текущий стрик: {streak_label} дней подряд",
        f"  За 7 дней: {last_7} тренировок",
        f"  За 30 дней: {last_30} тренировок",
        f"  За 90 дней: {last_90} тренировок",
    ]
    if last_7 >= 3:
        lines.append("\n💪 Стабильный график.")
    elif last_7 >= 1:
        lines.append("\n⚠️ Можно чаще — целься на 3+ в неделю.")
    else:
        lines.append("\n⚠️ За последнюю неделю не тренировался.")
    return "\n".join(lines)


async def _find_workout_by_exercise(telegram_user_id: str | None, parsed: dict) -> str:
    target = parsed.get("target") or {}
    ex_pat = target.get("exercise_name")
    if not ex_pat:
        return "Уточни упражнение: «когда я делал жим на 100»."
    min_w = target.get("weight_set_to")
    rows = await find_workouts_by_exercise(
        telegram_user_id, ex_pat,
        min_weight=float(min_w) if min_w else None,
        limit=15,
    )
    if not rows:
        cond = f" с весом >= {min_w} кг" if min_w else ""
        return f"Не нашёл тренировок с «{ex_pat}»{cond}."

    lines = [f"🔍 Тренировки с «{ex_pat}»" + (f" (≥ {min_w} кг)" if min_w else "") + ":", ""]
    for r in rows[:12]:
        d = str(r.get("workout_date"))[:10]
        lines.append(
            f"  {format_human_date(d)}: {r.get('exercise_name')} — "
            f"max {format_number(r.get('max_w'))} кг ({r.get('set_count')} подх.) {r.get('focus_label') or ''}"
        )
    return "\n".join(lines)


async def _show_trend(telegram_user_id: str | None, parsed: dict) -> str:
    target = parsed.get("target") or {}
    ex = target.get("exercise_name")
    if not ex:
        return "Уточни упражнение: «тренд по жиму»."
    days = int(target.get("days") or 90)
    rows = await find_workouts_by_exercise(telegram_user_id, ex, limit=50)
    if not rows:
        return f"Нет истории по «{ex}»."

    from datetime import timedelta
    today = date.today()
    cutoff = today - timedelta(days=days)

    filtered = []
    for r in rows:
        try:
            d = datetime.strptime(str(r["workout_date"])[:10], "%Y-%m-%d").date()
            if d >= cutoff and r.get("max_w"):
                filtered.append((d, float(r["max_w"])))
        except Exception:
            pass
    filtered.sort(key=lambda x: x[0])

    if len(filtered) < 2:
        return f"Мало данных по «{ex}» за {days} дней (нужно минимум 2 точки)."

    first_d, first_w = filtered[0]
    last_d, last_w = filtered[-1]
    delta = last_w - first_w
    pct = round(delta / first_w * 100, 1) if first_w else 0

    direction = "▲ растёт" if delta > 0 else ("▼ падает" if delta < 0 else "≈ стоит")

    lines = [
        f"📈 Тренд: {ex} за {days} дней",
        "",
        f"  Точек данных: {len(filtered)}",
        f"  Первое: {format_human_date(first_d.isoformat(), include_weekday=False)} — {format_number(first_w)} кг",
        f"  Последнее: {format_human_date(last_d.isoformat(), include_weekday=False)} — {format_number(last_w)} кг",
        f"  Дельта: {delta:+g} кг ({pct:+}%) {direction}",
    ]
    if len(filtered) >= 4:
        # Mini ASCII график (последние 8 точек)
        recent = filtered[-8:]
        max_w = max(p[1] for p in recent)
        min_w = min(p[1] for p in recent)
        rng = max_w - min_w if max_w > min_w else 1
        lines.append("")
        lines.append("  Последние:")
        for d, w in recent:
            bar_len = int((w - min_w) / rng * 20)
            lines.append(f"    {d.strftime('%d.%m')} {format_number(w):>5} {'█' * bar_len}")
    return "\n".join(lines)


async def _show_1rm(telegram_user_id: str | None, parsed: dict) -> str:
    target = parsed.get("target") or {}
    ex = target.get("exercise_name")
    if not ex:
        return "Уточни упражнение: «1ПМ жима лёжа»."
    rows = await find_workouts_by_exercise(telegram_user_id, ex, limit=10)
    if not rows:
        return f"Нет истории по «{ex}»."

    # Get best 1RM estimate across all sets of this exercise
    from app.db.engine import get_session as gs
    from sqlalchemy import text as sql_text
    async with gs() as s:
        result = await s.execute(sql_text("""
            SELECT s.weight_kg, s.reps, w.workout_date
            FROM fitness_exercise_sets s
            JOIN fitness_workouts w ON w.id = s.workout_id
            WHERE w.telegram_user_id = :uid
              AND lower(s.exercise_name) LIKE lower(:pat)
              AND s.weight_kg IS NOT NULL AND s.reps IS NOT NULL
            ORDER BY w.id DESC LIMIT 50
        """), {"uid": telegram_user_id, "pat": f"%{ex}%"})
        rows_db = result.mappings().all()

    if not rows_db:
        return f"Нет подходов с весом+повторами по «{ex}»."

    best = None
    for r in rows_db:
        est = estimate_1rm(r["weight_kg"], r["reps"])
        if not best or est > best[0]:
            best = (est, r["weight_kg"], r["reps"], r["workout_date"])
    if not best:
        return "Не удалось посчитать."

    est, w, r, d = best
    return (
        f"💪 Estimated 1RM для «{ex}»:\n\n"
        f"  ≈ {format_number(est)} кг (формула Epley)\n"
        f"  Основано на: {format_number(w)} кг × {r} ({format_human_date(str(d)[:10], include_weekday=False)})\n\n"
        f"Это оценка — реальный максимум проверяется в зале."
    )


async def _show_plateau(telegram_user_id: str | None, parsed: dict) -> str:
    """Find exercises that haven't progressed in N weeks."""
    target = parsed.get("target") or {}
    only_ex = target.get("exercise_name")

    from app.db.engine import get_session as gs
    from sqlalchemy import text as sql_text
    async with gs() as s:
        if only_ex:
            res = await s.execute(sql_text("""
                SELECT s.exercise_name,
                       MAX(s.weight_kg) AS max_w,
                       MAX(w.workout_date) AS last_at,
                       COUNT(DISTINCT w.id) AS sessions
                FROM fitness_exercise_sets s
                JOIN fitness_workouts w ON w.id = s.workout_id
                WHERE w.telegram_user_id = :uid
                  AND lower(s.exercise_name) LIKE lower(:pat)
                  AND s.weight_kg IS NOT NULL
                GROUP BY s.exercise_name
            """), {"uid": telegram_user_id, "pat": f"%{only_ex}%"})
        else:
            res = await s.execute(sql_text("""
                SELECT s.exercise_name,
                       MAX(s.weight_kg) AS max_w,
                       MAX(w.workout_date) AS last_at,
                       COUNT(DISTINCT w.id) AS sessions
                FROM fitness_exercise_sets s
                JOIN fitness_workouts w ON w.id = s.workout_id
                WHERE w.telegram_user_id = :uid
                  AND s.weight_kg IS NOT NULL
                  AND w.workout_date >= CURRENT_DATE - INTERVAL '60 days'
                GROUP BY s.exercise_name
                HAVING COUNT(DISTINCT w.id) >= 3
            """), {"uid": telegram_user_id})
        rows = [dict(r) for r in res.mappings().all()]

    if not rows:
        return "Недостаточно данных для plateau-анализа (нужно ≥3 тренировки на упражнение за 60 дней)."

    # For each, check if max weight was hit only in recent half (still progressing) or in old half (plateau)
    from datetime import timedelta
    today = date.today()

    plateau: list[dict] = []
    progressing: list[dict] = []
    for r in rows:
        ex_name = r["exercise_name"]
        max_w = r["max_w"]
        last_at = r["last_at"]
        sessions = r["sessions"]
        # Check: when was the max_w first reached?
        async with gs() as s2:
            first_max = await s2.execute(sql_text("""
                SELECT MIN(w.workout_date) AS first_d
                FROM fitness_exercise_sets s
                JOIN fitness_workouts w ON w.id = s.workout_id
                WHERE w.telegram_user_id = :uid
                  AND s.exercise_name = :ex
                  AND s.weight_kg = :w
            """), {"uid": telegram_user_id, "ex": ex_name, "w": max_w})
            first_d_row = first_max.first()
            first_d = first_d_row[0] if first_d_row else None
        if first_d:
            try:
                fd = first_d if hasattr(first_d, "isoformat") else datetime.strptime(str(first_d)[:10], "%Y-%m-%d").date()
                days_since = (today - fd).days
                if days_since >= 21:  # 3+ weeks без прогресса
                    plateau.append({"name": ex_name, "max_w": float(max_w), "days_since_pr": days_since, "sessions": sessions})
                else:
                    progressing.append({"name": ex_name, "max_w": float(max_w), "days_since_pr": days_since})
            except Exception:
                pass

    if not plateau:
        return "🟢 Плато не обнаружено — ты прогрессируешь по основным упражнениям!"

    plateau.sort(key=lambda x: -x["days_since_pr"])
    lines = ["⚠️ Плато (нет PR ≥ 3 недель):", ""]
    for p in plateau[:8]:
        lines.append(f"  • {p['name']}: {format_number(p['max_w'])} кг, без рекорда {p['days_since_pr']} дней ({p['sessions']} тренировок)")
    lines.append("")
    lines.append("💡 Попробуй: смена схемы (5×5 / 3×8 / drop set), deload-неделю, проверь технику.")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Пакет 4: Шаблоны и программирование
# ═══════════════════════════════════════════════════════════════════════


async def _save_template(
    telegram_user_id: str | None,
    parsed: dict,
    active_session: dict | None,
) -> str:
    target = parsed.get("target") or {}
    name = target.get("template_name")
    if not name:
        return "Уточни имя шаблона: «сохрани как шаблон 'грудь A'»."

    # Source: active workout or last workout
    workout_id = None
    if active_session and active_session.get("workout_id"):
        workout_id = int(active_session["workout_id"])
    else:
        last = await get_last_workout(telegram_user_id)
        if last:
            workout_id = last["workout"]["id"]
    if not workout_id:
        return "Нет тренировки для сохранения как шаблон."

    from app.db.engine import get_session as gs
    from sqlalchemy import text as sql_text
    async with gs() as s:
        w_res = await s.execute(sql_text("""
            SELECT focus, focus_label FROM fitness_workouts WHERE id = :id
        """), {"id": workout_id})
        w_row = w_res.mappings().first()
        sets_res = await s.execute(sql_text("""
            SELECT exercise_name, set_number, weight_kg, reps, notes
            FROM fitness_exercise_sets
            WHERE workout_id = :id ORDER BY id ASC
        """), {"id": workout_id})
        all_sets = [dict(r) for r in sets_res.mappings().all()]

    # Group sets into exercise targets
    by_ex: dict[str, list[dict]] = {}
    order: list[str] = []
    for st in all_sets:
        ex = st.get("exercise_name")
        if not ex:
            continue
        if ex not in by_ex:
            by_ex[ex] = []
            order.append(ex)
        by_ex[ex].append(st)

    exercises = []
    for i, ex in enumerate(order, start=1):
        sts = by_ex[ex]
        weights = [float(s["weight_kg"]) for s in sts if s.get("weight_kg")]
        reps = [int(s["reps"]) for s in sts if s.get("reps")]
        exercises.append({
            "exercise_order": i,
            "exercise_name": ex,
            "target_sets": len(sts),
            "target_reps_min": min(reps) if reps else None,
            "target_reps_max": max(reps) if reps else None,
            "target_weight_kg": max(weights) if weights else None,
        })

    tid = await save_workout_template(
        telegram_user_id=telegram_user_id,
        name=name,
        focus=(w_row or {}).get("focus") if w_row else None,
        focus_label=(w_row or {}).get("focus_label") if w_row else None,
        exercises=exercises,
        notes=f"Создан из тренировки #{workout_id}",
    )
    return f"💾 Сохранил шаблон '{name}' (ID #{tid}) с {len(exercises)} упражнениями."


async def _apply_template(telegram_user_id: str | None, parsed: dict) -> str:
    target = parsed.get("target") or {}
    name = target.get("template_name")
    if not name:
        return "Уточни имя шаблона: «примени шаблон 'грудь A' на завтра»."

    tpl = await get_workout_template_by_name(telegram_user_id, name)
    if not tpl:
        return f"Шаблон '{name}' не найден. Скажи «покажи шаблоны»."

    target_date = parsed.get("date") or date.today().isoformat()
    exercises = tpl.get("exercises_json") or []

    # Save as planned workout
    plan_id = await save_training_plan(
        telegram_user_id=telegram_user_id,
        plan_name=f"Шаблон: {tpl['name']}",
        period_type="day",
        start_date=target_date,
        end_date=target_date,
        source_text=f"Apply template #{tpl['id']}",
        notes=None,
        planned_workouts=[{
            "planned_date": target_date,
            "weekday": None,
            "sequence_number": 1,
            "is_floating": False,
            "title": tpl.get("focus_label") or tpl["name"],
            "focus": tpl.get("focus"),
            "focus_label": tpl.get("focus_label"),
            "workout_type": "custom",
            "status": "planned",
            "notes": None,
            "exercises": exercises,
        }],
    )
    await mark_template_used(tpl["id"])
    return f"📋 Применил шаблон '{tpl['name']}' на {format_human_date(target_date)} (план #{plan_id}, {len(exercises)} упр.)"


async def _list_templates_handler(telegram_user_id: str | None) -> str:
    tpls = await list_workout_templates(telegram_user_id)
    if not tpls:
        return "Шаблонов нет. Скажи «сохрани как шаблон 'имя'» после тренировки."
    lines = [f"📂 Шаблоны ({len(tpls)}):", ""]
    for t in tpls:
        ex_count = len(t.get("exercises_json") or [])
        last = t.get("last_used_at")
        last_str = f" · последний раз {str(last)[:10]}" if last else ""
        lines.append(f"  #{t['id']} '{t['name']}' — {t.get('focus_label') or '—'}, {ex_count} упр.{last_str}")
    return "\n".join(lines)


async def _bulk_edit_exercises(telegram_user_id: str | None, parsed: dict) -> str:
    target = parsed.get("target") or {}
    pat = target.get("exercise_name")
    if not pat:
        return "Уточни какие упражнения: «+5 кг ко всем жимам»."

    period = parsed.get("period") or {}
    s_d = period.get("start_date") or date.today().isoformat()
    e_d = period.get("end_date")  # may be None → no upper bound

    delta = target.get("weight_delta")
    w_set = target.get("weight_set_to")
    sets_to = target.get("sets_set_to")

    if all(x is None for x in (delta, w_set, sets_to)):
        return "Не понял что менять: вес/+/− или количество подходов."

    n = await bulk_update_planned_exercises(
        telegram_user_id=telegram_user_id,
        exercise_name_pattern=pat,
        weight_delta=float(delta) if delta is not None else None,
        weight_set=float(w_set) if w_set is not None else None,
        sets_set=int(sets_to) if sets_to is not None else None,
        start_date=s_d,
        end_date=e_d,
    )
    op = []
    if delta is not None:
        op.append(f"вес {delta:+g} кг")
    if w_set is not None:
        op.append(f"вес = {w_set} кг")
    if sets_to is not None:
        op.append(f"подходов = {sets_to}")
    return f"✏️ Обновил {n} упражнений «{pat}»: {', '.join(op)}."


async def _set_goal(telegram_user_id: str | None, parsed: dict, text: str) -> str:
    target = parsed.get("target") or {}
    ex = target.get("exercise_name")
    val = target.get("goal_value")
    deadline = target.get("goal_deadline")
    goal_type = target.get("goal_type") or ("exercise_max" if ex and val else "custom")

    if not val and not ex:
        return "Сформулируй цель чётче: «цель: жим 100 кг к декабрю»."

    gid = await add_fitness_goal(
        telegram_user_id=telegram_user_id,
        goal_type=goal_type,
        target_exercise=ex,
        target_value=float(val) if val else None,
        target_unit="kg",
        target_deadline=deadline,
        notes=text[:300],
    )
    dl_str = f" к {format_human_date(deadline)}" if deadline else ""
    target_str = f"{ex} {val} кг" if ex and val else (ex or f"{val} {goal_type}")
    return f"🎯 Цель #{gid}: {target_str}{dl_str}. Запомнил."


async def _show_goals(telegram_user_id: str | None) -> str:
    goals = await list_active_goals(telegram_user_id)
    if not goals:
        return "Активных целей нет. Поставь: «цель: жим 100 кг к декабрю»."

    lines = [f"🎯 Активные цели ({len(goals)}):", ""]
    for g in goals:
        target_v = g.get("target_value")
        ex = g.get("target_exercise") or "—"
        dl = g.get("target_deadline")
        # Check current best
        cur_best = None
        if ex and ex != "—":
            from app.db.engine import get_session as gs
            from sqlalchemy import text as sql_text
            async with gs() as s:
                r = await s.execute(sql_text("""
                    SELECT MAX(s.weight_kg) FROM fitness_exercise_sets s
                    JOIN fitness_workouts w ON w.id = s.workout_id
                    WHERE w.telegram_user_id = :uid AND lower(s.exercise_name) LIKE lower(:pat)
                """), {"uid": telegram_user_id, "pat": f"%{ex}%"})
                cur_best = r.scalar()

        progress_str = ""
        if target_v and cur_best:
            pct = min(100, round(float(cur_best) / float(target_v) * 100, 1))
            progress_str = f" · текущий max {format_number(cur_best)} ({pct}%)"
        dl_str = f" → к {format_human_date(dl)}" if dl else ""

        lines.append(f"  #{g['id']} {ex}: {format_number(target_v) if target_v else '?'} кг{dl_str}{progress_str}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Пакет 5: Отчёты
# ═══════════════════════════════════════════════════════════════════════


async def _schedule_reminder_action(
    telegram_user_id: str | None,
    parsed: dict,
    text: str,
) -> str:
    target = parsed.get("target") or {}
    note = target.get("note_text") or text[:200]
    target_date = parsed.get("date") or date.today().isoformat()
    time_hm = target.get("time_hh_mm")
    recurrence = target.get("recurrence")

    if not time_hm:
        # Try regex from text
        m = re.search(r"\b(\d{1,2})[:.](\d{2})\b", text)
        if m:
            time_hm = f"{int(m.group(1)):02d}:{m.group(2)}"
        else:
            # word forms: "в 7 утра", "в 18", "в 9 вечера"
            m2 = re.search(r"\bв\s+(\d{1,2})(?:\s+(утра|вечера|ночи|дня))?", text.lower())
            if m2:
                h = int(m2.group(1))
                part = m2.group(2)
                if part == "вечера" and h < 12:
                    h += 12
                if part == "ночи" and h < 6:
                    h += 0  # 1 ночи = 1:00
                if part == "утра" and h >= 12:
                    h -= 12
                time_hm = f"{h:02d}:00"

    if not time_hm:
        return "Уточни время: «напомни в 7:00» или «в 18:30»."

    # Build fire_at in UTC
    from datetime import timezone as tz
    try:
        d = datetime.strptime(target_date, "%Y-%m-%d").date()
    except Exception:
        d = date.today()
    hh, mm = [int(x) for x in time_hm.split(":")]
    # Assume user's local = UTC for now (bot is single-user). Could be improved.
    fire_dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz.utc)

    # if past — push to tomorrow
    now = datetime.now(tz.utc)
    if fire_dt <= now:
        from datetime import timedelta as _td
        fire_dt = fire_dt + _td(days=1)

    kind = "custom"
    if any(x in text.lower() for x in ["тренировк", "трен ", "трене", "workout"]):
        kind = "workout_today"

    rid = await schedule_reminder(
        telegram_user_id=telegram_user_id,
        fire_at=fire_dt,  # передаём datetime, asyncpg сконвертит сам
        kind=kind,
        payload={"text": note[:300]},
        recurrence=recurrence,
    )
    rec_str = f" ({recurrence})" if recurrence else ""
    return (
        f"⏰ Напоминание #{rid} на {format_human_date(fire_dt.date().isoformat())} {time_hm}{rec_str}.\n"
        f"Текст: {note[:100]}"
    )


async def _show_measurements_trend(telegram_user_id: str | None, parsed: dict) -> str:
    target = parsed.get("target") or {}
    days = int(target.get("days") or 90)
    rows = await get_measurements_period(telegram_user_id, days)
    if not rows:
        return f"Замеров за последние {days} дней нет. Запиши: «вес 80», «талия 82»."

    lines = [f"📊 Замеры за последние {days} дней:", ""]
    fields = [
        ("weight_kg", "Вес", "кг"),
        ("waist_cm", "Талия", "см"),
        ("chest_cm", "Грудь", "см"),
        ("hips_cm", "Бёдра", "см"),
        ("arm_cm", "Рука", "см"),
        ("thigh_cm", "Бедро", "см"),
        ("neck_cm", "Шея", "см"),
        ("bodyfat_pct", "% жира", "%"),
    ]
    for key, label, unit in fields:
        values = [(r["measurement_date"], r[key]) for r in rows if r.get(key) is not None]
        if not values:
            continue
        first_d, first_v = values[0]
        last_d, last_v = values[-1]
        delta = float(last_v) - float(first_v)
        sign = "▲" if delta > 0 else ("▼" if delta < 0 else "≈")
        lines.append(
            f"  {label}: {format_number(first_v)}{unit} → {format_number(last_v)}{unit} "
            f"({sign} {delta:+g}{unit}, {len(values)} замеров)"
        )

        # mini ASCII trend (только если 4+ замера)
        if len(values) >= 4:
            recent = values[-8:]
            vs = [float(v) for _, v in recent]
            vmax = max(vs)
            vmin = min(vs)
            rng = vmax - vmin if vmax > vmin else 1
            for d, v in recent:
                bar = int((float(v) - vmin) / rng * 15)
                lines.append(f"    {str(d)[:10]} {format_number(v):>6} {'█' * bar}")
            lines.append("")

    return "\n".join(lines)


async def _coach_report(telegram_user_id: str | None, parsed: dict) -> str:
    """Красивый отчёт для тренера: недельная сводка + ключевые цифры."""
    s, e = await _resolve_period(parsed, default_days=7)
    workouts = await get_completed_workouts_in_period(telegram_user_id, s, e)

    if not workouts:
        return f"За {s} — {e} тренировок не записано. Нечего отправлять тренеру."

    all_sets = []
    for w in workouts:
        for st in (w.get("sets") or []):
            all_sets.append(st)
    by_group = aggregate_by_group(all_sets)

    total_tonnage = sum(g["tonnage"] for g in by_group.values())
    total_sets = sum(g["sets"] for g in by_group.values())

    lines = [
        f"📋 ОТЧЁТ ТРЕНЕРУ",
        f"Период: {format_human_date(s, include_weekday=False)} — {format_human_date(e, include_weekday=False)}",
        "=" * 40,
        "",
        f"Тренировок: {len(workouts)}",
        f"Всего подходов: {total_sets}",
        f"Общий тоннаж: {format_number(round(total_tonnage, 0))} кг",
        "",
        "Объём по группам:",
    ]
    for g, st in sorted(by_group.items(), key=lambda x: -x[1]["tonnage"]):
        lines.append(f"  {g}: {st['sets']} подх. · {format_number(round(st['tonnage'], 0))} кг")
    lines.append("")
    lines.append("Тренировки:")
    for w in workouts:
        d = format_human_date(w.get("workout_date"))
        focus = w.get("focus_label") or "—"
        n_sets = len(w.get("sets") or [])
        lines.append(f"  · {d}: {focus} ({n_sets} подходов)")
        if w.get("notes"):
            lines.append(f"      📝 {w['notes'][:100]}")

    # Recent PRs
    from app.db.engine import get_session as gs
    from sqlalchemy import text as sql_text
    async with gs() as ss:
        prs = await ss.execute(sql_text("""
            SELECT s.exercise_name, MAX(s.weight_kg) AS w
            FROM fitness_exercise_sets s
            JOIN fitness_workouts w ON w.id = s.workout_id
            WHERE w.telegram_user_id = :uid
              AND w.workout_date BETWEEN :s AND :e
              AND s.weight_kg IS NOT NULL
            GROUP BY s.exercise_name
            ORDER BY MAX(s.weight_kg) DESC LIMIT 8
        """), {"uid": telegram_user_id, "s": s, "e": e})
        pr_rows = [dict(r) for r in prs.mappings().all()]
    if pr_rows:
        lines.append("")
        lines.append("Лучшие веса:")
        for r in pr_rows:
            lines.append(f"  · {r['exercise_name']}: {format_number(r['w'])} кг")

    lines.append("")
    lines.append("— конец отчёта —")
    return "\n".join(lines)


async def _weekly_summary(telegram_user_id: str | None) -> str:
    """Сводка текущей недели + сравнение с прошлой."""
    from datetime import timedelta
    cur_s, cur_e = week_bounds()
    cur_s_d = datetime.strptime(cur_s, "%Y-%m-%d").date()
    prev_s = (cur_s_d - timedelta(days=7)).isoformat()
    prev_e = (cur_s_d - timedelta(days=1)).isoformat()

    cur = await get_completed_workouts_in_period(telegram_user_id, cur_s, cur_e)
    prev = await get_completed_workouts_in_period(telegram_user_id, prev_s, prev_e)

    def stats(workouts):
        ts = 0
        tg = 0.0
        ex_set = set()
        for w in workouts:
            for st in (w.get("sets") or []):
                ts += 1
                ex_set.add(st.get("exercise_name"))
                try:
                    if st.get("weight_kg") and st.get("reps"):
                        tg += float(st["weight_kg"]) * int(st["reps"])
                except Exception:
                    pass
        return len(workouts), ts, round(tg, 0), len(ex_set)

    cw, cs, ct, ce = stats(cur)
    pw, ps, pt, pe = stats(prev)

    def diff(a, b):
        d = a - b
        sign = "▲" if d > 0 else ("▼" if d < 0 else "≈")
        return f"{a} ({sign} {d:+})"

    lines = [
        f"📅 Сводка недели {format_human_date(cur_s, include_weekday=False)} — {format_human_date(cur_e, include_weekday=False)}",
        "",
        f"  Тренировок: {diff(cw, pw)}",
        f"  Подходов: {diff(cs, ps)}",
        f"  Тоннаж: {diff(ct, pt)} кг",
        f"  Упражнений уникальных: {ce}",
    ]
    if cw == 0:
        lines.append("\n⚠️ На этой неделе ещё не тренировался.")
    elif cw >= pw:
        lines.append("\n💪 Стабильная неделя.")
    else:
        lines.append("\n⚠️ Меньше тренировок чем прошлая неделя.")
    return "\n".join(lines)


async def _mark_workout_completed(workout_id: int) -> None:
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text
    async with get_session() as s:
        await s.execute(
            sql_text("UPDATE fitness_workouts SET completion_type='completed' WHERE id = :id"),
            {"id": workout_id},
        )
        await s.commit()


async def _finish_workout_with_summary(telegram_user_id: str | None, workout_id: int) -> str:
    """Закрыть сессию + богатое резюме: подходы, тоннаж, длительность, новые PR."""
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text
    from datetime import timedelta

    async with get_session() as s:
        w = await s.execute(sql_text("""
            SELECT id, workout_date, focus_label, focus, created_at, notes
            FROM fitness_workouts WHERE id = :id
        """), {"id": workout_id})
        wrow = w.mappings().first()
        if not wrow:
            return f"Тренировка #{workout_id} не найдена."

        sets = await s.execute(sql_text("""
            SELECT exercise_name, set_number, weight_kg, reps, notes
            FROM fitness_exercise_sets
            WHERE workout_id = :id ORDER BY id ASC
        """), {"id": workout_id})
        all_sets = [dict(r) for r in sets.mappings().all()]

    # Resolve pending session
    pending = await get_latest_fitness_pending_decision(telegram_user_id)
    started_iso = None
    if pending:
        ctx = pending.get("context_json") or {}
        started_iso = ctx.get("started_at")
        if pending.get("id"):
            await resolve_fitness_pending_decision(pending["id"], status="resolved")

    # Mark workout as completed (so router_hardening не считает её активной)
    await _mark_workout_completed(workout_id)

    # Aggregate
    total_sets = len(all_sets)
    by_ex: dict[str, list[dict]] = {}
    total_tonnage = 0.0
    max_per_ex: dict[str, float] = {}
    for st in all_sets:
        ex = st.get("exercise_name") or "—"
        by_ex.setdefault(ex, []).append(st)
        if st.get("weight_kg") and st.get("reps"):
            try:
                total_tonnage += float(st["weight_kg"]) * int(st["reps"])
            except Exception:
                pass
            try:
                w = float(st["weight_kg"])
                if w > max_per_ex.get(ex, 0):
                    max_per_ex[ex] = w
            except Exception:
                pass

    # PR detection отключено по требованию пользователя
    # Duration
    duration_str = ""
    if started_iso:
        try:
            started = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
            dur = datetime.now(timezone.utc) - started
            total_min = int(dur.total_seconds() // 60)
            h, m = divmod(total_min, 60)
            duration_str = f"{h} ч {m} мин" if h else f"{m} мин"
        except Exception:
            pass

    lines = [
        f"✅ Тренировка завершена! ID: #{workout_id}",
        "",
        f"📅 Дата: {format_human_date(wrow.get('workout_date'))}",
    ]
    if wrow.get("focus_label"):
        lines.append(f"🎯 Фокус: {wrow['focus_label']}")
    if duration_str:
        lines.append(f"⏱ Длительность: {duration_str}")
    lines.append(f"💪 Упражнений: {len(by_ex)}, подходов: {total_sets}")
    if total_tonnage > 0:
        lines.append(f"📊 Тоннаж: {format_number(round(total_tonnage, 1))} кг")
    if wrow.get("notes"):
        lines.append("")
        lines.append(f"📝 {wrow['notes']}")

    return "\n".join(lines)


async def _undo_last_action(telegram_user_id: str | None) -> str:
    """Reverse the last write action, if possible."""
    last = await get_last_interaction(telegram_user_id)
    if not last:
        return "Нечего отменять — нет истории."
    action = (last.get("action") or "").lower()

    if action in ("log_workout_sets", "continue_current_exercise", "log_workout_sets_append"):
        pending = await get_latest_fitness_pending_decision(telegram_user_id)
        if not pending:
            return "Не нашёл активную сессию для undo."
        wid = (pending.get("context_json") or {}).get("workout_id")
        if not wid:
            return "Нет workout_id для undo."
        n = await delete_last_n_sets(int(wid), 1)
        return f"↩️ Отменил — удалил последний подход ({n})."

    # Plan-edit undo: ищем последнее событие в planned_workout_events
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text
    target_date = last.get("current_workout_date")

    async with get_session() as s:
        if target_date:
            ev = await s.execute(sql_text("""
                SELECT e.id, e.planned_workout_id, e.event_type, e.old_value_json, e.new_value_json
                FROM planned_workout_events e
                JOIN planned_workouts pw ON pw.id = e.planned_workout_id
                WHERE pw.telegram_user_id = :uid AND pw.planned_date = :d
                ORDER BY e.created_at DESC LIMIT 1
            """), {"uid": telegram_user_id, "d": target_date})
        else:
            ev = await s.execute(sql_text("""
                SELECT e.id, e.planned_workout_id, e.event_type, e.old_value_json, e.new_value_json
                FROM planned_workout_events e
                ORDER BY e.created_at DESC LIMIT 1
            """))
        event = ev.mappings().first()
        if not event:
            return "Не нашёл последнее изменение плана для отката."

        et = event["event_type"]
        old = event.get("old_value_json") or {}
        if isinstance(old, str):
            import json as _j
            try:
                old = _j.loads(old)
            except Exception:
                old = {}

        if et == "exercise_replaced":
            # Откатить: вернуть old_exercise_name + старые параметры (если в old_value есть)
            ex_id = old.get("exercise_id")
            old_name = old.get("exercise_name")
            if not ex_id or not old_name:
                return "Не смог откатить замену — данных недостаточно."
            await s.execute(sql_text("""
                UPDATE planned_exercises
                SET exercise_name = :n
                WHERE id = :id
            """), {"n": old_name, "id": ex_id})
            await s.commit()
            return f"↩️ Откатил замену: вернул «{old_name}»."

    return (
        "Undo пока умею только для записи подходов и замены упражнений. "
        "Для остального скажи явно: «удали последний подход», «не 80 а 85»."
    )


async def _add_set_note(
    telegram_user_id: str | None,
    parsed: dict,
    active_session: dict | None,
) -> str:
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    target = parsed.get("target") or {}
    note_text = target.get("note_text")
    set_number = target.get("set_number")

    if not note_text:
        return "Текст заметки пуст."
    if not active_session or not active_session.get("workout_id"):
        last = await get_last_workout(telegram_user_id)
        if not last:
            return "Нет активной сессии и записанных тренировок."
        wid = last["workout"]["id"]
    else:
        wid = int(active_session["workout_id"])

    async with get_session() as s:
        if set_number:
            res = await s.execute(sql_text("""
                UPDATE fitness_exercise_sets
                SET notes = COALESCE(notes || ' | ', '') || :n
                WHERE workout_id = :w AND set_number = :sn
                RETURNING id, exercise_name
            """), {"w": wid, "sn": int(set_number), "n": note_text})
        else:
            # last set
            res = await s.execute(sql_text("""
                UPDATE fitness_exercise_sets
                SET notes = COALESCE(notes || ' | ', '') || :n
                WHERE id = (
                    SELECT id FROM fitness_exercise_sets
                    WHERE workout_id = :w ORDER BY id DESC LIMIT 1
                )
                RETURNING id, exercise_name
            """), {"w": wid, "n": note_text})
        row = res.mappings().first()
        await s.commit()
    if not row:
        return "Не нашёл подход для заметки."
    return f"📝 Заметка к подходу ({row['exercise_name']}): {note_text}"


async def _tag_feeling(
    telegram_user_id: str | None,
    text: str,
    parsed: dict,
    active_session: dict | None,
) -> str:
    """Tag current/last workout with how user felt."""
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    target = parsed.get("target") or {}
    feeling = target.get("feeling") or text.strip()
    tag_line = f"#feeling: {feeling}"

    if active_session and active_session.get("workout_id"):
        wid = int(active_session["workout_id"])
    else:
        last = await get_last_workout(telegram_user_id)
        if not last:
            return f"Запомнил: {feeling}. Записать к какой тренировке — не нашёл."
        wid = last["workout"]["id"]

    async with get_session() as s:
        await s.execute(sql_text("""
            UPDATE fitness_workouts
            SET notes = COALESCE(notes || E'\n', '') || :tag
            WHERE id = :id
        """), {"id": wid, "tag": tag_line})
        await s.commit()
    return f"📌 Записал к тренировке #{wid}: {tag_line}"


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


async def _format_active_session_contents(
    telegram_user_id: str | None,
    active_session: dict,
) -> str:
    """Display all recorded sets in the current active session."""
    workout_id = active_session.get("workout_id")
    if not workout_id:
        return "📋 Активная сессия идёт, но данных ещё нет."

    wdata = await get_last_workout(telegram_user_id)
    if not wdata or str(wdata["workout"].get("id")) != str(workout_id):
        return "📋 Сессия активна, подходов ещё не записано."

    sets = wdata.get("sets") or []
    if not sets:
        return "📋 Сессия активна, подходов ещё не записано."

    from collections import defaultdict as _dd
    by_ex = _dd(list)
    for s in sets:
        by_ex[s["exercise_name"]].append(s)
    lines = [f"📋 Текущая сессия #{workout_id}:"]
    for ex, ex_sets in by_ex.items():
        parts = []
        for s in ex_sets:
            w = s.get("weight_kg")
            r = s.get("reps")
            n = s.get("notes") or ""
            if w:
                parts.append(f"{w}×{r}" if r else f"{w}кг")
            elif r:
                parts.append(f"{r} повт.")
            elif n:
                parts.append(n[:20])
        lines.append(f"  • {ex}: {', '.join(parts)}")
    lines.append(f"\nПодходов: {len(sets)}")
    return "\n".join(lines)


async def _edit_last_set(
    telegram_user_id: str | None,
    parsed: dict,
    active_session: dict | None,
    text: str = "",
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
    exercise_name = target.get("exercise_name")

    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    async with get_session() as session:
        row = None
        rec = None

        # "Не 80, а 82.5" → старый вес — НАИБОЛЕЕ ТОЧНЫЙ сигнал. Проверяем ПЕРВЫМ,
        # до exercise_name, т.к. AI мог подтянуть exercise_name из контекста (последнее упр.)
        # и он будет неправильным. Явное "не 80" всегда важнее AI-угадки упражнения.
        _m_old = re.search(r'\bне\s+(\d+(?:[.,]\d+)?)\b', (text or '').lower())
        _old_w = float(_m_old.group(1).replace(',', '.')) if (_m_old and field == 'weight_kg') else None

        if _old_w is not None:
            _r1 = await session.execute(sql_text("""
                SELECT id, exercise_name, weight_kg, reps
                FROM fitness_exercise_sets
                WHERE workout_id = :wid AND weight_kg = :w
                ORDER BY id DESC LIMIT 1
            """), {"wid": workout_id, "w": _old_w})
            rec = _r1.mappings().first()
            if rec is None:
                # Точный вес не нашли — fallback на последний подход
                _r2 = await session.execute(sql_text("""
                    SELECT id, exercise_name, weight_kg, reps
                    FROM fitness_exercise_sets
                    WHERE workout_id = :wid
                    ORDER BY id DESC LIMIT 1
                """), {"wid": workout_id})
                rec = _r2.mappings().first()
        elif exercise_name and set_number:
            row = await session.execute(sql_text("""
                SELECT id, exercise_name, weight_kg, reps
                FROM fitness_exercise_sets
                WHERE workout_id = :wid
                  AND LOWER(exercise_name) LIKE LOWER(:exn)
                  AND set_number = :sn
                ORDER BY id DESC LIMIT 1
            """), {"wid": workout_id, "exn": f"%{exercise_name}%", "sn": int(set_number)})
        elif exercise_name:
            row = await session.execute(sql_text("""
                SELECT id, exercise_name, weight_kg, reps
                FROM fitness_exercise_sets
                WHERE workout_id = :wid
                  AND LOWER(exercise_name) LIKE LOWER(:exn)
                ORDER BY id DESC LIMIT 1
            """), {"wid": workout_id, "exn": f"%{exercise_name}%"})
        elif set_number:
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

        # For branches that set row (not rec directly)
        if row is not None:
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
