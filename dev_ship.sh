#!/usr/bin/env bash
set -euo pipefail

MSG="${1:-Dev ship}"

echo "== 1. Python syntax check =="
python3 -m py_compile \
  app/db.py \
  app/version.py \
  app/telegram_bot.py \
  app/main.py \
  app/modules/ops/status.py \
  app/modules/fitness/*.py \
  scripts/smoke_fitness.py

echo
echo "== 2. Fitness regression smoke =="
python3 scripts/smoke_fitness.py

echo
echo "== 3. Internal parser scenarios =="
python3 scripts/run_internal_scenarios.py

echo
echo "== 4. Stage all changed files =="
git add -A

echo
echo "== 5. Existing ship =="
./ship.sh "$MSG"

echo
echo "== 6. Coolify deploy =="
if [[ "${FORCE_COOLIFY_WEBHOOK:-}" == "1" ]]; then
  if [[ -f ".env" ]]; then
    set -a
    source .env
    set +a
  fi

  if [[ -n "${COOLIFY_DEPLOY_WEBHOOK:-}" ]]; then
    if [[ -n "${COOLIFY_TOKEN:-}" ]]; then
      curl -fsS -X GET "$COOLIFY_DEPLOY_WEBHOOK" \
        -H "Authorization: Bearer $COOLIFY_TOKEN" || \
      curl -fsS -X POST "$COOLIFY_DEPLOY_WEBHOOK" \
        -H "Authorization: Bearer $COOLIFY_TOKEN"
    else
      curl -fsS -X GET "$COOLIFY_DEPLOY_WEBHOOK" || \
      curl -fsS -X POST "$COOLIFY_DEPLOY_WEBHOOK"
    fi
    echo
    echo "Coolify deploy triggered by FORCE_COOLIFY_WEBHOOK=1"
  else
    echo "FORCE_COOLIFY_WEBHOOK=1 set, but COOLIFY_DEPLOY_WEBHOOK is missing"
  fi
else
  echo "Skipping manual webhook. Coolify Git App should auto-deploy after push."
  echo "To force webhook deploy: FORCE_COOLIFY_WEBHOOK=1 ./dev_ship.sh \"message\""
fi


echo
echo "== 7. Last commits =="
git log --oneline -3
