#!/bin/bash
set -e

MESSAGE="$1"

if [ -z "$MESSAGE" ]; then
  echo "Usage: ./ship.sh \"commit message\""
  exit 1
fi

echo ""
echo "== 1. Python syntax check =="
python3 -m py_compile \
  app/db.py \
  app/telegram_bot.py \
  app/modules/fitness/handler.py \
  app/modules/fitness/parser.py \
  app/modules/fitness/change_parser.py \
  app/modules/fitness/pending_parser.py \
  app/modules/fitness/pending_plan_parser.py \
  app/modules/fitness/action_v2.py \
  app/modules/fitness/router_hardening.py \
  app/modules/fitness/planned_workout_executor.py \
  app/modules/fitness/planned_workout_editor.py \
  app/modules/fitness/planned_workout_parser.py \
  app/modules/fitness/custom_workout_builder.py \
  app/modules/fitness/exercise_history.py \
  app/modules/fitness/exercise_normalizer.py \
  app/modules/fitness/formatter.py \
  app/modules/fitness/utils.py \
  app/modules/ops/status.py

echo ""
echo "== 2. Dangerous SQL pattern check =="

BAD_SQL=0

if grep -R -nE ":[a-zA-Z_][a-zA-Z0-9_]* IS NULL|:[a-zA-Z_][a-zA-Z0-9_]* IS NOT NULL|CASE WHEN :[a-zA-Z_][a-zA-Z0-9_]* IS NULL" app/db.py; then
  echo "ERROR: dangerous asyncpg SQL pattern found."
  BAD_SQL=1
fi

if grep -R -nE "jsonb_build_object\\([^)]*:" app/db.py; then
  echo "WARNING: possible risky jsonb_build_object with bind params. Review manually."
fi

if [ "$BAD_SQL" -eq 1 ]; then
  exit 1
fi

echo ""
echo "== 3. Required Telegram commands check =="

python3 - <<'PY'
from pathlib import Path

s = Path("app/telegram_bot.py").read_text()

required = [
    'CommandHandler("status"',
    'CommandHandler("today_workout"',
    'CommandHandler("next_workout"',
    'CommandHandler("week_plan"',
    'CommandHandler("next_week_plan"',
    'CommandHandler("month_plan"',
    'CommandHandler("next_month_plan"',
    'CommandHandler("fitness_debug_week"',
    'CommandHandler("fitness_debug_next_week"',
    'CommandHandler("fitness_debug_month"',
    'CommandHandler("fitness_reset_week"',
]

missing = [x for x in required if x not in s]

if missing:
    print("ERROR: missing command handlers:")
    for m in missing:
        print(" -", m)
    raise SystemExit(1)

print("Telegram command handlers: OK")
PY

echo ""
echo "== 4. Fitness router smoke test =="

python3 - <<'PY'
from app.modules.fitness.utils import is_likely_fitness_text

cases = [
    ("Перенеси спину на субботу", True),
    ("Дай тренировку на грудь", True),
    ("Какая тренировка сегодня?", True),
    ("Покажи следующую неделю тренировок", True),
    ("Третий 20 на 10", False),
    ("Плечи замени на грудь", True),
    ("Убери присед из сегодняшней тренировки", True),
    ("Перенеси встречу на субботу", False),
    ("Покажи документы по проекту", False),
]

failed = []

for text, expected in cases:
    actual = is_likely_fitness_text(text)
    if actual != expected:
        failed.append((text, expected, actual))

if failed:
    print("ERROR: fitness router smoke test failed:")
    for text, expected, actual in failed:
        print(f" - {text!r}: expected {expected}, got {actual}")
    raise SystemExit(1)

from app.modules.fitness.exercise_normalizer import normalize_exercise_name
from app.modules.fitness.exercise_history import handle_exercise_history_request
assert normalize_exercise_name("жим гантелей сидя")["exercise_key"] == "seated_dumbbell_press"
assert normalize_exercise_name("жим на плечи")["exercise_key"] == "seated_dumbbell_press"
assert normalize_exercise_name("махи в стороны")["exercise_key"] in ("lateral_raise_seated", "lateral_raise_standing")

from app.modules.fitness.router_hardening import handle_router_hardening
assert callable(handle_router_hardening)
from app.modules.fitness.custom_workout_builder import parse_custom_workout_details, create_custom_workout_from_details
assert callable(parse_custom_workout_details)
assert callable(create_custom_workout_from_details)
print("Custom workout builder imports: OK")
from app.modules.fitness.planned_workout_parser import parse_planned_workout_action
from app.modules.fitness.planned_workout_executor import execute_planned_workout_action
assert callable(parse_planned_workout_action)
assert callable(execute_planned_workout_action)
from app.modules.fitness.planned_workout_editor import parse_workout_edit_action
assert callable(parse_workout_edit_action)
print("Planned workout editor imports: OK")
print("Planned workout parser/executor imports: OK")
print("Router hardening imports: OK")
print("Exercise history imports: OK")
print("Fitness router: OK")
PY

echo ""
echo "== 5. Git status =="
git status

echo ""
echo "== 6. Git diff summary =="
git diff --stat

echo ""
echo "== 7. Commit =="
git add app ship.sh

if git diff --cached --quiet; then
  echo "Nothing to commit."
else
  git commit -m "$MESSAGE"
fi

echo ""
echo "== 8. Push =="
git push

echo ""
echo "DONE."
echo "Now redeploy in Coolify."
echo ""
git log --oneline -3
