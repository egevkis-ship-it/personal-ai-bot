from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.modules.fitness.program_import_executor import (
    _build_dates_by_weekdays,
    _build_dates_every_other_day,
    _parse_weekday_layout,
)
from app.modules.fitness.utils import is_likely_fitness_text

from app.modules.fitness.program_importer import (
    format_program_import_preview,
    parse_training_program_text,
)

try:
    from app.modules.fitness.router_hardening import _parse_pending_cancel_confirmation
except Exception as exc:
    raise RuntimeError(f"Cannot import cancel confirmation parser: {exc}") from exc


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"{label}: expected to contain {needle!r}")


def test_program_preview() -> None:
    sample = """
ПРОГРАММА ТРЕНИРОВОК

День 1
1. Жим штанги лёжа (ВИДЕО) 25-20-20-20-20-15-15
2. Жим гантелей под углом (ВИДЕО) 5*15

День 3
1. Жим гантелей сидя (ВИДЕО) 25-20-15-12-8
2. Задняя дельта в кроссовере (ВИДЕО) + Махи на среднюю дельту стоя (ВИДЕО) 4*12
"""
    program = parse_training_program_text(sample)
    preview = format_program_import_preview(program)

    assert_contains(preview, "День 1 — грудь", "day 1 focus")
    assert_contains(preview, "День 3 — плечи", "day 3 focus")
    assert_contains(preview, "2A. Задняя дельта в кроссовере — 4×12", "superset A")
    assert_contains(preview, "2B. Махи на среднюю дельту стоя — 4×12", "superset B")


def test_program_layout() -> None:
    assert_equal(_parse_weekday_layout("пн вт чт пт", 4), [0, 1, 3, 4], "weekday short layout")
    assert_equal(_parse_weekday_layout("пн ср пт сб", 4), [0, 2, 4, 5], "weekday standard layout")

    dates = _build_dates_by_weekdays([0, 1, 3, 4], 1)
    if len(dates) != 4:
        raise AssertionError(f"expected 4 dates for weekday import, got {dates}")

    every_other = _build_dates_every_other_day(4)
    if len(every_other) != 4:
        raise AssertionError(f"expected 4 dates for every-other-day import, got {every_other}")


def test_cancel_confirmation_parser() -> None:
    for text in ["да", "отмени", "отмена", "отменяй", "удали", "удаляй", "подтверждаю"]:
        assert_equal(_parse_pending_cancel_confirmation(text), "confirm", f"confirm {text}")

    for text in ["не надо", "стоп", "не трогай", "оставь", "ничего не делай"]:
        assert_equal(_parse_pending_cancel_confirmation(text), "reject", f"reject {text}")

    assert_equal(_parse_pending_cancel_confirmation("покажи план"), "unknown", "unknown unrelated command")



def test_fitness_pre_router_copy_commands() -> None:
    truthy = [
        "скопируй на следующую неделю",
        "продублируй на следующую неделю",
        "скопируй на весь месяц",
        "повтори на следующий месяц",
    ]

    for text in truthy:
        assert_equal(is_likely_fitness_text(text), True, f"fitness pre-router {text}")

    assert_equal(is_likely_fitness_text("скопируй файл"), False, "non-fitness copy file")


def main() -> None:
    test_program_preview()
    test_program_layout()
    test_cancel_confirmation_parser()
    test_fitness_pre_router_copy_commands()
    print("Fitness smoke tests: OK")


if __name__ == "__main__":
    main()
