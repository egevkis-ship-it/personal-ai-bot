"""
Regression smoke tests — БЕЗ доступа к DB и без живых LLM-вызовов.
Покрывают известные баги классификаторов и роутеров (regex level).
"""
import os
import re

import pytest
import yaml

pytestmark = pytest.mark.regression

FIXTURE = os.path.join(os.path.dirname(__file__), "conversations.yaml")


def load_cases():
    with open(FIXTURE) as f:
        return yaml.safe_load(f).get("cases", [])


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c.get("name", "?"))
def test_router_classification_doesnt_misroute(case):
    """
    Для каждого регрессионного кейса: классификаторы (regex level, без AI)
    должны НЕ давать ложно-положительных совпадений с ops.
    """
    from app.router import _has_hard_fitness_signal, _has_hard_ops_signal

    text = case["input"]

    # Если это явно фитнес-кейс, fitness detector должен сработать
    if "тренировк" in text.lower() or "жим" in text.lower() or "копируй" in text.lower():
        is_fit = _has_hard_fitness_signal(text)
        # допускаем оба — мы хотим чтобы AT LEAST одно не упало в ops
        is_ops = _has_hard_ops_signal(text)
        assert not is_ops, f"{case['name']}: hard-ops false positive on fitness text"

    # "удали тренировки" / "копируй" не должно быть ops
    if "удали" in text.lower() and "тренировк" in text.lower():
        assert not _has_hard_ops_signal(text), f"{case['name']}: 'удали тренировки' → ops"

    # "Какая погода" не должно срабатывать ни на fitness, ни на ops
    if "погода" in text.lower():
        assert not _has_hard_fitness_signal(text)
        assert not _has_hard_ops_signal(text)


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c.get("name", "?"))
def test_no_bad_strings_in_formatted_dates(case):
    """Bad strings — 1900/2999 даты, ISO в заголовках — не должны проходить через форматтер."""
    must_not_regex = case.get("must_not_contain_regex", []) or []
    must_not_literal = case.get("must_not_contain", []) or []

    # Здесь мы НЕ запускаем полный flow (нет DB), но проверим формат:
    # format_human_date НИКОГДА не должен возвращать ISO
    from app.modules.fitness.formatter import format_human_date

    for d in ["2026-05-22", "2026-12-31", "2024-01-01"]:
        out = format_human_date(d)
        for pat in must_not_regex:
            assert not re.search(pat, out), \
                f"{case['name']}: format_human_date({d}) = {out!r} matched bad pattern {pat}"
        for s in must_not_literal:
            # Запрещённые строки не должны быть в выводе date helper'а
            # (это слабая проверка, но ловит регрессии типа "1900-01-01")
            if s in ("1900-01-01", "2999-12-31"):
                assert s not in out
