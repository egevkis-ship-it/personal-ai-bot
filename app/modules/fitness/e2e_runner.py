"""
In-process E2E test runner.

Запускает сценарии из tests/e2e_scenarios.yaml через реальный handler chain
с тестовым user_id (изолированные данные). Использует реальную БД + реальный Claude.

Запуск:
  - Telegram-командой /run_tests от owner
  - Scheduled task (daily cron) — отдельно
  - Из CLI: python -m app.modules.fitness.e2e_runner
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Тестовый "пользователь" — данные изолированы от реального
TEST_USER_ID = "__e2e_test_runner__"

# app/modules/fitness/e2e_runner.py → ../../../tests/e2e_scenarios.yaml
SCENARIOS_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "tests", "e2e_scenarios.yaml",
))


def load_scenarios() -> list[dict]:
    with open(SCENARIOS_FILE) as f:
        return (yaml.safe_load(f) or {}).get("scenarios", [])


def _assert_step(response: str, step: dict) -> tuple[bool, str]:
    """Returns (passed, reason). reason is empty if passed."""
    must = step.get("must_contain") or []
    must_not = step.get("must_not_contain") or []
    must_regex = step.get("must_contain_regex") or []
    must_not_regex = step.get("must_not_contain_regex") or []

    for needle in must:
        if needle.lower() not in response.lower():
            return False, f"missing required: {needle!r}"
    for bad in must_not:
        if bad.lower() in response.lower():
            return False, f"contains forbidden: {bad!r}"
    for pat in must_regex:
        if not re.search(pat, response, re.IGNORECASE):
            return False, f"regex not matched: {pat!r}"
    for pat in must_not_regex:
        if re.search(pat, response, re.IGNORECASE):
            return False, f"forbidden regex matched: {pat!r}"
    return True, ""


async def _cleanup_test_data(user_id: str) -> None:
    """Удалить все следы test_user_id из БД."""
    from app.db.engine import get_session
    from sqlalchemy import text as sql_text

    tables_user = [
        "fitness_workouts", "body_measurements",
        "planned_workouts", "training_plans",
        "fitness_pending_decisions",
        "training_constraints", "pain_journal",
        "fitness_goals", "workout_templates",
        "learning_corrections", "user_preferences",
        "last_interaction", "scheduled_reminders",
    ]
    async with get_session() as s:
        for t in tables_user:
            try:
                await s.execute(
                    sql_text(f"DELETE FROM {t} WHERE telegram_user_id = :uid"),
                    {"uid": user_id},
                )
            except Exception:
                pass
        await s.commit()


async def run_scenarios(scenarios: list[dict] | None = None) -> dict:
    """Execute all scenarios, return summary."""
    from app.modules.fitness.action_v2 import handle_fitness_action_v2
    from app.bot_reply import BotReply

    if scenarios is None:
        scenarios = load_scenarios()

    results = []
    start = time.time()

    # Очистка перед запуском
    await _cleanup_test_data(TEST_USER_ID)

    for scenario in scenarios:
        name = scenario.get("name", "unnamed")
        steps = scenario.get("steps") or []
        step_results = []
        scenario_passed = True

        for i, step in enumerate(steps, start=1):
            send_text = step.get("send", "")
            t0 = time.time()
            try:
                response = await asyncio.wait_for(
                    handle_fitness_action_v2(TEST_USER_ID, send_text),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                step_results.append({
                    "step": i, "send": send_text,
                    "passed": False, "reason": "timeout >60s",
                })
                scenario_passed = False
                continue
            except Exception as e:
                step_results.append({
                    "step": i, "send": send_text,
                    "passed": False, "reason": f"crashed: {type(e).__name__}: {e}",
                })
                scenario_passed = False
                continue

            elapsed = time.time() - t0
            resp_text = response.text if isinstance(response, BotReply) else (response or "")
            ok, reason = _assert_step(resp_text, step)
            step_results.append({
                "step": i, "send": send_text,
                "passed": ok, "reason": reason,
                "response_preview": resp_text[:200],
                "elapsed_s": round(elapsed, 2),
            })
            if not ok:
                scenario_passed = False

        results.append({
            "name": name,
            "passed": scenario_passed,
            "steps": step_results,
        })

    # Очистка после
    await _cleanup_test_data(TEST_USER_ID)

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "elapsed_s": round(time.time() - start, 2),
        "results": results,
    }


def format_report(summary: dict, verbose: bool = False) -> str:
    """Human-readable report for Telegram chat (4096 char safe)."""
    total = summary["total"]
    passed = summary["passed"]
    failed = summary["failed"]
    icon = "✅" if failed == 0 else "❌"

    lines = [
        f"{icon} E2E тесты: {passed}/{total} passed",
        f"⏱ {summary['elapsed_s']}s",
        "",
    ]
    for r in summary["results"]:
        mark = "✅" if r["passed"] else "❌"
        lines.append(f"{mark} {r['name']}")
        if not r["passed"] or verbose:
            for s in r["steps"]:
                step_mark = "  ✓" if s["passed"] else "  ✗"
                lines.append(f"{step_mark} step {s['step']}: {s.get('send', '')[:60]}")
                if not s["passed"]:
                    lines.append(f"      reason: {s.get('reason')}")
                    lines.append(f"      resp: {s.get('response_preview', '')[:120]}")
    out = "\n".join(lines)
    return out[:4000] + ("\n... (обрезано)" if len(out) > 4000 else "")


async def main():
    summary = await run_scenarios()
    print(format_report(summary, verbose=True))


if __name__ == "__main__":
    asyncio.run(main())
