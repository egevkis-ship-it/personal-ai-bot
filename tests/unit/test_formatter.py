"""Unit tests for format functions — no DB/LLM."""
from datetime import date

import pytest

from app.modules.fitness.formatter import (
    format_human_date,
    format_completed_period,
    format_personal_records,
    format_planned_workout,
    format_number,
)


pytestmark = pytest.mark.unit


class TestFormatHumanDate:
    def test_iso_string(self):
        assert format_human_date("2026-05-18") == "18-05-2026 — понедельник"

    def test_iso_no_weekday(self):
        assert format_human_date("2026-05-21", include_weekday=False) == "21-05-2026"

    def test_none(self):
        assert format_human_date(None) == "без даты"

    def test_empty(self):
        assert format_human_date("") == "без даты"

    def test_date_object(self):
        assert format_human_date(date(2026, 5, 18)) == "18-05-2026 — понедельник"

    def test_no_iso_in_output(self):
        """Регресс: 'Тренировка на 2026-05-22' должен превращаться в человеческий формат."""
        out = format_human_date("2026-05-22")
        assert "2026" in out  # год тоже там
        assert "пятница" in out
        # YYYY-MM-DD старый формат — НЕ должно быть на первом месте
        assert not out.startswith("2026-")


class TestFormatNumber:
    def test_integer(self):
        assert format_number(80) == "80"

    def test_float_integer(self):
        assert format_number(80.0) == "80"

    def test_float_decimal(self):
        assert format_number(17.5) == "17.5"

    def test_none(self):
        assert format_number(None) == ""


class TestFormatCompletedPeriod:
    def test_empty(self):
        out = format_completed_period([], "Сегодня")
        assert "ещё не записано" in out

    def test_with_data(self):
        workouts = [{
            "workout_date": "2026-05-18",
            "focus_label": "грудь",
            "bodyweight_kg": 80,
            "notes": None,
            "sets": [
                {"exercise_name": "Жим штанги", "weight_kg": 80, "reps": 5},
                {"exercise_name": "Жим штанги", "weight_kg": 80, "reps": 5},
            ],
        }]
        out = format_completed_period(workouts, "Сегодня")
        assert "Жим штанги" in out
        assert "тоннаж" in out.lower()
        assert "80×5" in out

    def test_none_exercise_name_fallback(self):
        """Регресс: пустое имя не должно ломать форматтер."""
        workouts = [{
            "workout_date": "2026-05-18",
            "focus_label": "—",
            "sets": [{"exercise_name": None, "weight_kg": None, "reps": 10}],
        }]
        out = format_completed_period(workouts, "Test")
        assert "Упражнение" in out


class TestFormatPersonalRecords:
    def test_empty(self):
        assert "рекордов" in format_personal_records([])

    def test_with_data(self):
        prs = [{"exercise_name": "Жим", "max_weight": 100, "best_reps": 5, "sessions": 12}]
        out = format_personal_records(prs)
        assert "100" in out
        assert "Жим" in out
        assert "12" in out


class TestFormatPlannedWorkout:
    def test_basic(self):
        data = {
            "workout": {
                "title": "Грудь",
                "planned_date": "2026-05-18",
                "focus_label": "грудь",
                "status": "planned",
                "notes": None,
            },
            "exercises": [
                {
                    "exercise_name": "Жим",
                    "target_sets": 4,
                    "target_reps_min": 8,
                    "target_reps_max": 12,
                    "target_weight_kg": 80,
                    "notes": None,
                }
            ],
        }
        out = format_planned_workout(data)
        assert "18-05-2026 — понедельник" in out
        assert "4×8-12" in out

    def test_notes_displayed(self):
        data = {
            "workout": {"title": "Test", "planned_date": "2026-05-18", "status": "planned", "notes": "Не забыть лямки"},
            "exercises": [],
        }
        out = format_planned_workout(data)
        assert "Не забыть лямки" in out

    def test_no_iso_in_date_line(self):
        """Регресс: 'Дата: 2026-05-22' → должно быть '22-05-2026 — пятница'."""
        data = {
            "workout": {"title": "X", "planned_date": "2026-05-22", "status": "planned"},
            "exercises": [],
        }
        out = format_planned_workout(data)
        # Ищем строку Дата:
        for line in out.split("\n"):
            if line.startswith("Дата:"):
                assert "пятница" in line, f"Дата без weekday: {line!r}"
                assert "22-05-2026" in line, f"Дата не в формате DD-MM-YYYY: {line!r}"
                return
        pytest.fail("Не нашёл строку 'Дата:' в выводе")
