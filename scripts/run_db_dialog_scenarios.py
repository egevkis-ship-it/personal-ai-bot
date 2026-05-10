from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


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

    return None


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
