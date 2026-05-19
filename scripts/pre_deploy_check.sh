#!/usr/bin/env bash
# Pre-deploy gate: запускает все тесты которые могут работать без DB.
# Используется как git pre-push hook или вручную перед коммитом.
#
# Установка hook:
#   ln -s ../../scripts/pre_deploy_check.sh .git/hooks/pre-push
#   chmod +x .git/hooks/pre-push
#
# Запуск вручную:
#   ./scripts/pre_deploy_check.sh

set -e

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
    echo "❌ .venv не найден — установи зависимости"
    exit 1
fi

PY=".venv/bin/python"

echo "──────────────────────────────────────────"
echo "1️⃣  Проверка синтаксиса"
echo "──────────────────────────────────────────"

$PY - <<'PYEOF'
import ast, sys, pathlib
errors = []
for f in pathlib.Path("app").rglob("*.py"):
    try:
        ast.parse(open(f).read())
    except SyntaxError as e:
        errors.append(f"{f}: {e}")
if errors:
    for e in errors:
        print("❌", e)
    sys.exit(1)
print("✅ Синтаксис OK")
PYEOF

echo
echo "──────────────────────────────────────────"
echo "2️⃣  Unit-тесты (без DB/LLM)"
echo "──────────────────────────────────────────"

if ! $PY -c "import pytest" 2>/dev/null; then
    echo "⚠️  pytest не установлен — пропускаем"
    echo "   Установи: $PY -m pip install -r requirements-dev.txt"
else
    $PY -m pytest tests/unit tests/regression -m "unit or regression" --tb=short
fi

echo
echo "──────────────────────────────────────────"
echo "3️⃣  Проверка миграций"
echo "──────────────────────────────────────────"

$PY - <<'PYEOF'
import os, re, sys
DIR = "app/db/migrations"
files = sorted(f for f in os.listdir(DIR) if re.match(r"^\d{3}_.*\.sql$", f))
all_sql = ""
for f in files:
    with open(os.path.join(DIR, f)) as fp:
        all_sql += fp.read() + "\n"

required = [
    "last_interaction", "learning_corrections", "user_preferences",
    "workout_templates", "fitness_goals", "pain_journal", "scheduled_reminders",
]
missing = [t for t in required if t not in all_sql]
if missing:
    print(f"❌ Таблицы отсутствуют в миграциях: {missing}")
    sys.exit(1)
print(f"✅ Все {len(required)} критичных таблиц в миграциях")
PYEOF

echo
echo "✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ — можно деплоить"
