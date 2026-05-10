from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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


def parse_cancel_confirmation(text: str) -> str:
    from app.modules.fitness.router_hardening import _parse_pending_cancel_confirmation

    return _parse_pending_cancel_confirmation(text)


def check_step(step: dict[str, Any]) -> tuple[bool, str]:
    text = step.get("send") or ""

    if step.get("parser") == "cancel_confirmation":
        decision = parse_cancel_confirmation(text)
        expected = step.get("expect_decision")
        if decision != expected:
            return False, f'send={text!r}: expected decision {expected!r}, got {decision!r}'
        return True, f'send={text!r}: decision={decision}'

    action = parse_action(text)
    action_name = action.get("action") if action else None

    if "expect_action" in step:
        expected = step["expect_action"]
        if action_name != expected:
            return False, f'send={text!r}: expected action {expected!r}, got {action_name!r}; action={action!r}'

    for key in ["expect_scope", "expect_start_date", "expect_end_date"]:
        if key in step:
            action_key = key.replace("expect_", "")
            expected_value = step[key]
            actual_value = action.get(action_key) if action else None
            if actual_value != expected_value:
                return False, (
                    f'send={text!r}: expected {action_key}={expected_value!r}, '
                    f'got {actual_value!r}; action={action!r}'
                )

    return True, f'send={text!r}: action={action_name}'


def main() -> None:
    scenario_paths = [Path(p) for p in sys.argv[1:]]
    if not scenario_paths:
        scenario_paths = sorted(Path("tests/dialog_scenarios").glob("*.json"))

    if not scenario_paths:
        raise SystemExit("No dialog scenario files found.")

    total = 0
    failed = 0

    for path in scenario_paths:
        data = load_json(path)
        print(f"Dialog scenario: {data.get('name') or path.name}")

        for i, step in enumerate(data.get("steps") or [], start=1):
            total += 1
            ok, message = check_step(step)
            if ok:
                print(f"  ✅ step {i}: {message}")
            else:
                failed += 1
                print(f"  ❌ step {i}: {message}")

    if failed:
        raise SystemExit(f"Dialog scenarios failed: {failed}/{total}")

    print(f"Dialog scenarios: OK ({total}/{total})")


if __name__ == "__main__":
    main()
