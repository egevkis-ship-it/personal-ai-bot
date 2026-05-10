from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _parse_action(text: str) -> dict[str, Any] | None:
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


def _parse_cancel_confirmation(text: str) -> str:
    from app.modules.fitness.router_hardening import _parse_pending_cancel_confirmation

    return _parse_pending_cancel_confirmation(text)


def _check_case(case: dict[str, Any]) -> tuple[bool, str]:
    name = case.get("name") or "unnamed"
    text = case.get("input") or ""

    if case.get("parser") == "cancel_confirmation":
        decision = _parse_cancel_confirmation(text)
        expected = case.get("expect_decision")
        if decision != expected:
            return False, f"{name}: expected decision {expected!r}, got {decision!r}"
        return True, f"{name}: OK"

    action = _parse_action(text)
    action_name = action.get("action") if action else None

    if "expect_action" in case:
        expected = case["expect_action"]
        if action_name != expected:
            return False, f"{name}: expected action {expected!r}, got {action_name!r}; action={action!r}"

    if "expect_not_action" in case:
        forbidden = case["expect_not_action"]
        if action_name == forbidden:
            return False, f"{name}: forbidden action {forbidden!r}; action={action!r}"

    if "expect_action_any" in case:
        allowed = set(case["expect_action_any"] or [])
        if action_name not in allowed:
            return False, f"{name}: expected any of {sorted(allowed)!r}, got {action_name!r}; action={action!r}"

    return True, f"{name}: OK"


def main() -> None:
    scenario_paths = [Path(p) for p in sys.argv[1:]]
    if not scenario_paths:
        scenario_paths = sorted(Path("tests/scenarios").glob("*.json"))

    if not scenario_paths:
        raise SystemExit("No scenario files found.")

    total = 0
    failed = 0

    for path in scenario_paths:
        data = _load_json(path)
        print(f"Scenario: {data.get('name') or path.name}")

        for case in data.get("cases") or []:
            total += 1
            ok, message = _check_case(case)
            if ok:
                print(f"  ✅ {message}")
            elif case.get("todo"):
                reason = case.get("todo_reason") or "known TODO"
                print(f"  ⚠️  TODO {message} — {reason}")
            else:
                failed += 1
                print(f"  ❌ {message}")

    if failed:
        raise SystemExit(f"Internal scenarios failed: {failed}/{total}")

    print(f"Internal scenarios: OK ({total}/{total})")


if __name__ == "__main__":
    main()
