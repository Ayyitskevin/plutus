#!/usr/bin/env bash
# Pull latest main and restart Plutus homelab studio service (:8030).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PLUTUS_HOMELAB_PORT:-8030}"
BRANCH="${PLUTUS_DEPLOY_BRANCH:-main}"

echo "==> pytest studio gate"
if [[ -x "$ROOT/.venv/bin/pytest" ]]; then
  PLUTUS_DATABASE_URL= bash scripts/ci-smoke.sh
else
  echo "WARN: .venv missing — run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
fi

echo "==> git pull origin $BRANCH"
git pull origin "$BRANCH"

if systemctl --user is-enabled plutus-homelab.service >/dev/null 2>&1; then
  echo "==> restart plutus-homelab.service"
  systemctl --user restart plutus-homelab.service
else
  echo "==> service not installed — run: bash scripts/install-homelab-service.sh" >&2
  exit 1
fi

sleep 2
systemctl --user is-active plutus-homelab.service
curl -sf "http://127.0.0.1:${PORT}/healthz" | python3 -m json.tool | head -25
echo "==> Deploy OK — Plutus studio :${PORT} (Mise admin feature)"