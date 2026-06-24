#!/usr/bin/env bash
# Pull latest main and restart the user-level Plutus SaaS service (homelab prod).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PLUTUS_PORT:-8031}"
BRANCH="${PLUTUS_DEPLOY_BRANCH:-main}"

if [[ ! -f "$ROOT/.venv/bin/pytest" ]]; then
  echo "WARN: .venv missing — run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev,saas]'" >&2
else
  echo "==> pytest (quick gate)"
  PLUTUS_DATABASE_URL= "$ROOT/.venv/bin/pytest" -q
fi

echo "==> git pull origin $BRANCH"
git pull origin "$BRANCH"

if systemctl --user is-enabled plutus-saas.service >/dev/null 2>&1; then
  echo "==> restart plutus-saas.service"
  systemctl --user restart plutus-saas.service
else
  echo "==> service not installed — run: bash scripts/install-saas-service.sh" >&2
  exit 1
fi

sleep 2
systemctl --user is-active plutus-saas.service
curl -sf "http://127.0.0.1:${PORT}/healthz" | python3 -m json.tool | head -20
echo "==> Deploy OK — https://plutus.kleephotography.com (if tunnel wired)"