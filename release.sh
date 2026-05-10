#!/usr/bin/env bash
set -euo pipefail

BUMP="${1:-patch}"
MSG="${2:-Release}"

if [[ "$BUMP" != "major" && "$BUMP" != "minor" && "$BUMP" != "patch" && "$BUMP" != "none" ]]; then
  echo "Usage: ./release.sh [major|minor|patch|none] \"commit message\""
  exit 1
fi

echo "== 1. Bump version =="
if [[ "$BUMP" != "none" ]]; then
  python3 scripts/bump_version.py "$BUMP"
else
  python3 scripts/bump_version.py none
fi

echo
echo "== 2. Python syntax check =="
python3 -m py_compile \
  app/db.py \
  app/version.py \
  app/telegram_bot.py \
  app/main.py \
  app/modules/ops/status.py \
  app/modules/fitness/*.py \
  scripts/bump_version.py \
  scripts/smoke_fitness.py

echo
echo "== 3. Fitness regression smoke =="
python3 scripts/smoke_fitness.py

echo
echo "== 4. Existing ship =="
./ship.sh "$MSG"

echo
echo "== 5. Coolify deploy webhook =="
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
  echo "Coolify deploy triggered"
else
  echo "COOLIFY_DEPLOY_WEBHOOK not set; redeploy manually"
fi

echo
echo "== 6. Last commits =="
git log --oneline -3
