"""Unit tests for the E2E scenario assertion logic."""
import os

import pytest

pytestmark = pytest.mark.unit


class TestAssertStep:
    @pytest.fixture(autouse=True)
    def setup(self):
        from app.modules.fitness.e2e_runner import _assert_step
        self.check = _assert_step

    def test_must_contain_pass(self):
        ok, _ = self.check("Это тренировка", {"must_contain": ["тренировка"]})
        assert ok

    def test_must_contain_fail(self):
        ok, reason = self.check("Это тренировка", {"must_contain": ["велосипед"]})
        assert not ok
        assert "велосипед" in reason

    def test_must_not_contain_pass(self):
        ok, _ = self.check("Всё хорошо", {"must_not_contain": ["ошибка"]})
        assert ok

    def test_must_not_contain_fail(self):
        ok, reason = self.check("Произошла ошибка", {"must_not_contain": ["ошибка"]})
        assert not ok
        assert "ошибка" in reason.lower()

    def test_must_contain_regex_pass(self):
        ok, _ = self.check("18-05-2026", {"must_contain_regex": [r"\d{2}-\d{2}-\d{4}"]})
        assert ok

    def test_must_not_contain_regex_pass(self):
        ok, _ = self.check("18-05-2026", {"must_not_contain_regex": [r"\d{4}-\d{2}-\d{2}"]})
        assert ok

    def test_must_not_contain_regex_fail(self):
        ok, reason = self.check("2026-05-18", {"must_not_contain_regex": [r"\d{4}-\d{2}-\d{2}"]})
        assert not ok


class TestLoadScenarios:
    def test_yaml_loadable(self):
        from app.modules.fitness.e2e_runner import load_scenarios
        scenarios = load_scenarios()
        assert isinstance(scenarios, list)
        assert len(scenarios) > 0
        for s in scenarios:
            assert "name" in s
            assert "steps" in s
            assert isinstance(s["steps"], list)

    def test_scenarios_valid_format(self):
        from app.modules.fitness.e2e_runner import load_scenarios
        for scenario in load_scenarios():
            for i, step in enumerate(scenario["steps"]):
                assert "send" in step, f"{scenario['name']} step {i}: missing 'send'"
                # at least one assertion type
                keys = step.keys()
                has_assert = any(k in keys for k in [
                    "must_contain", "must_not_contain",
                    "must_contain_regex", "must_not_contain_regex",
                ])
                assert has_assert, f"{scenario['name']} step {i}: no assertions"


class TestFormatReport:
    def test_all_passed(self):
        from app.modules.fitness.e2e_runner import format_report
        summary = {
            "total": 2, "passed": 2, "failed": 0, "elapsed_s": 1.5,
            "results": [
                {"name": "A", "passed": True, "steps": []},
                {"name": "B", "passed": True, "steps": []},
            ],
        }
        out = format_report(summary)
        assert "✅" in out
        assert "2/2" in out

    def test_some_failed(self):
        from app.modules.fitness.e2e_runner import format_report
        summary = {
            "total": 2, "passed": 1, "failed": 1, "elapsed_s": 1.5,
            "results": [
                {"name": "A", "passed": True, "steps": []},
                {"name": "B", "passed": False, "steps": [
                    {"step": 1, "send": "test", "passed": False, "reason": "boom"}
                ]},
            ],
        }
        out = format_report(summary)
        assert "❌" in out
        assert "1/2" in out
        assert "boom" in out
