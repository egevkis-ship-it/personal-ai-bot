from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_dbcheck_env() -> None:
    """
    Load optional .env.dbcheck without adding another dependency.
    Format:
      DATABASE_URL='...'
      REDIS_URL='...'
    """
    env_file = PROJECT_ROOT / ".env.dbcheck"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


load_dbcheck_env()

# DB scenarios do not call Telegram or OpenAI directly.
# These dummy values only allow app.config.Settings() to load locally.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dbcheck_dummy_telegram_token")
os.environ.setdefault("OPENAI_API_KEY", "dbcheck_dummy_openai_key")


def ensure_db_dependencies() -> None:
    missing = []

    for module_name in ["sqlalchemy", "asyncpg", "greenlet"]:
        try:
            __import__(module_name)
        except ModuleNotFoundError:
            missing.append(module_name)

    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(
            "DB dialog scenarios require local DB dependencies.\n"
            f"Missing: {missing_list}\n\n"
            "Install project dependencies first, for example:\n"
            "  uv venv --python 3.12 .venv\n"
            "  source .venv/bin/activate\n"
            "  uv pip install -r requirements.txt\n"
            "  uv pip install greenlet\n\n"
            "Then run:\n"
            "  ./botctl dbcheck"
        )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _next_weekday_date(weekday: int) -> str:
    d = date.today()
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d.isoformat()


def _fallback_db_dialog_action(text: str) -> dict[str, Any] | None:
    """
    Fallback for phrases production router handles, but fast parser may not.
    Used only by DB scenario runner.
    """
    t = (text or "").strip().lower().replace("ё", "е")

    if (
        any(x in t for x in ["покажи", "показать", "дай", "выведи", "посмотри"])
        and any(x in t for x in ["тренировку", "тренировка", "треньку", "треню"])
    ):
        weekday_map = {
            "понедельник": 0,
            "пн": 0,
            "вторник": 1,
            "вт": 1,
            "сред": 2,
            "ср": 2,
            "четверг": 3,
            "чт": 3,
            "пятниц": 4,
            "пт": 4,
            "суббот": 5,
            "сб": 5,
            "воскрес": 6,
            "вс": 6,
        }

        for key, weekday in weekday_map.items():
            if key in t:
                target_date = _next_weekday_date(weekday)
                return {
                    "action": "show_period_plan",
                    "confidence": 0.95,
                    "scope": "date",
                    "start_date": target_date,
                    "end_date": target_date,
                    "summary": f"Показать тренировку на {target_date}",
                }

    return None


def parse_action(text: str) -> dict[str, Any] | None:
    from app.modules.fitness.planned_workout_parser import fast_parse_planning_action

    action = fast_parse_planning_action(text)
    if action:
        return action

    try:
        from app.modules.fitness.planned_workout_editor import fast_parse_workout_edit

        action = fast_parse_workout_edit(text)
        if action:
            return action
    except Exception:
        pass

    fallback = _fallback_db_dialog_action(text)
    if fallback:
        return fallback

    return None


async def set_selected_context_for_date(user_id: str, target_date: str, source_text: str = "db dialog scenario") -> None:
    """
    Store selected workout context for DB dialog scenarios.

    Copy only needs target_date, but edit flows need planned_workout_id.
    Therefore this runner resolves the real active planned_workout row by date.
    """
    from datetime import date as date_type

    from sqlalchemy import text

    from app.db import AsyncSessionLocal, create_fitness_pending_decision

    target_date_value = date_type.fromisoformat(target_date) if isinstance(target_date, str) else target_date

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT id, planned_date, title, focus, focus_label
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND planned_date = :target_date
                  AND status = 'planned'
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {
                "telegram_user_id": str(user_id),
                "target_date": target_date_value,
            },
        )
        row = result.mappings().first()

    if not row:
        await create_fitness_pending_decision(
            telegram_user_id=user_id,
            decision_type="selected_planned_workout_context",
            context={
                "planned_workout_id": None,
                "target_date": target_date,
                "title": "Кастомная тренировка",
                "focus": "full_body",
                "focus_label": "full body",
            },
            source_text=source_text,
        )
        return

    planned_date = row["planned_date"]
    planned_date_s = planned_date.isoformat() if hasattr(planned_date, "isoformat") else str(planned_date)

    await create_fitness_pending_decision(
        telegram_user_id=user_id,
        decision_type="selected_planned_workout_context",
        context={
            "planned_workout_id": int(row["id"]),
            "target_date": planned_date_s,
            "title": row["title"] or "Кастомная тренировка",
            "focus": row["focus"] or "full_body",
            "focus_label": row["focus_label"] or "full body",
        },
        source_text=source_text,
    )


async def seed_scenario(user_id: str, seed: dict[str, Any]) -> str:
    seed_type = seed.get("type")

    if seed_type == "custom_workout":
        from app.db import save_training_plan

        target_date = seed.get("target_date") or "2026-05-11"
        source_text = seed.get("text") or "dbcheck seed workout"

        exercises = [
            "Жим штанги лёжа",
            "Приседания со штангой",
            "Становая тяга",
            "Отжимания",
            "Пресс подъёмы корпуса, ноги согнуты",
            "Велосипед",
        ]

        plan_id = await save_training_plan(
            telegram_user_id=user_id,
            plan_name="DB Check Seed Plan",
            period_type="single_day",
            start_date=target_date,
            end_date=target_date,
            source_text=source_text,
            notes="Created by db dialog scenario seed",
            planned_workouts=[
                {
                    "planned_date": target_date,
                    "weekday": "понедельник",
                    "sequence_number": 1,
                    "is_floating": False,
                    "title": "Кастомная тренировка",
                    "focus": "full_body",
                    "focus_label": "full body",
                    "workout_type": "planned",
                    "status": "planned",
                    "notes": "DB dialog scenario seed workout",
                    "exercises": [
                        {
                            "exercise_order": i,
                            "exercise_name": name,
                            "target_sets": None,
                            "target_reps_min": None,
                            "target_reps_max": None,
                            "target_reps_text": None,
                            "target_weight_kg": None,
                            "notes": None,
                        }
                        for i, name in enumerate(exercises, start=1)
                    ],
                }
            ],
        )

        await set_selected_context_for_date(
            user_id=user_id,
            target_date=target_date,
            source_text="db dialog scenario seed",
        )

        return f"Создал seed тренировку на {target_date}. ID плана: {plan_id}"

    raise RuntimeError(f"Unsupported seed type: {seed_type!r}")


async def run_step(user_id: str, step: dict[str, Any]) -> tuple[bool, str]:
    from app.modules.fitness.planned_workout_executor import execute_planned_workout_action

    text = step.get("send") or ""
    action = parse_action(text)

    if not action:
        return False, f"send={text!r}: parser returned None"

    reply = await execute_planned_workout_action(
        telegram_user_id=user_id,
        action=action,
        source_text=text,
    )

    reply = reply or ""

    if (
        action.get("action") == "show_period_plan"
        and action.get("scope") == "date"
        and action.get("start_date")
    ):
        await set_selected_context_for_date(
            user_id=user_id,
            target_date=action["start_date"],
            source_text=text,
        )

    if "expect_contains" in step:
        expected = step["expect_contains"]
        if expected not in reply:
            return False, (
                f"send={text!r}: expected reply to contain {expected!r}\n"
                f"action={action!r}\n"
                f"reply={reply!r}"
            )

    if "expect_contains_all" in step:
        for expected in step["expect_contains_all"]:
            if expected not in reply:
                return False, (
                    f"send={text!r}: expected reply to contain {expected!r}\n"
                    f"action={action!r}\n"
                    f"reply={reply!r}"
                )

    if "expect_contains_any" in step:
        expected_values = step["expect_contains_any"] or []
        if not any(expected in reply for expected in expected_values):
            return False, (
                f"send={text!r}: expected reply to contain any of {expected_values!r}\n"
                f"action={action!r}\n"
                f"reply={reply!r}"
            )

    if "expect_not_contains" in step:
        forbidden = step["expect_not_contains"]
        if forbidden in reply:
            return False, (
                f"send={text!r}: expected reply NOT to contain {forbidden!r}\n"
                f"action={action!r}\n"
                f"reply={reply!r}"
            )

    return True, f"send={text!r}: OK"


async def run_scenario(path: Path) -> tuple[int, int]:
    data = load_json(path)

    # Synthetic user. Does not touch real Egor data.
    user_id = f"db_dialog_test_{uuid.uuid4().hex[:12]}"

    print(f"DB dialog scenario: {data.get('name') or path.name}")
    print(f"  test_user={user_id}")

    seed = data.get("seed")
    if seed:
        seed_reply = await seed_scenario(user_id, seed)
        first_line = seed_reply.splitlines()[0] if seed_reply else "seed done"
        print(f"  🌱 seed: {first_line}")

    total = 0
    failed = 0

    for i, step in enumerate(data.get("steps") or [], start=1):
        total += 1
        ok, message = await run_step(user_id, step)

        if ok:
            print(f"  ✅ step {i}: {message}")
        elif step.get("todo"):
            reason = step.get("todo_reason") or "known TODO"
            print(f"  ⚠️  TODO step {i}: {message} — {reason}")
        else:
            failed += 1
            print(f"  ❌ step {i}: {message}")

    return total, failed


async def amain() -> None:
    ensure_db_dependencies()

    scenario_paths = [Path(p) for p in sys.argv[1:]]
    if not scenario_paths:
        scenario_paths = sorted(Path("tests/db_dialog_scenarios").glob("*.json"))

    if not scenario_paths:
        raise SystemExit("No DB dialog scenario files found.")

    total = 0
    failed = 0

    for path in scenario_paths:
        scenario_total, scenario_failed = await run_scenario(path)
        total += scenario_total
        failed += scenario_failed

    if failed:
        raise SystemExit(f"DB dialog scenarios failed: {failed}/{total}")

    print(f"DB dialog scenarios: OK ({total}/{total}; TODO failures ignored)")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
