#!/usr/bin/env bash
# Full money loop dogfood (GitHub/CI reference): checkout → pay → lab → track.
# Uses signed webhook in live Stripe mode; simulate in test mode.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TARGET="${PLUTUS_DOGFOOD_TARGET:-saas}"
if [[ "$TARGET" == "homelab" ]]; then
  export PLUTUS_ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}"
  export PLUTUS_PORT=8030
  bash "$ROOT/scripts/dogfood-homelab-money.sh"
  export PLUTUS_DOGFOOD_ORDER_ID="${PLUTUS_DOGFOOD_ORDER_ID:-}"
  bash "$ROOT/scripts/dogfood-lab-fulfillment.sh"
else
  bash "$ROOT/scripts/dogfood-phase6.sh"
  ORDER_ID=$(python3 - <<PY
import os, sys
from pathlib import Path
sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
from dotenv import load_dotenv
load_dotenv("${PLUTUS_ENV_FILE:-${ROOT}/.env}", override=True)
from app import db
db.migrate()
rows = db.list_orders(limit=1)
print(rows[0]["id"] if rows else "")
PY
)
  export PLUTUS_DOGFOOD_ORDER_ID="$ORDER_ID"
  PLUTUS_ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}" PLUTUS_PORT=8031 \
    bash "$ROOT/scripts/dogfood-lab-fulfillment.sh" || true
fi

if [[ -n "${PLUTUS_ORDER_WEBHOOK_URL:-}" ]]; then
  bash "$ROOT/scripts/dogfood-notifications.sh"
fi

if [[ -n "${PLUTUS_SMTP_HOST:-}" ]]; then
  bash "$ROOT/scripts/dogfood-smtp.sh"
fi

echo "==> Money loop dogfood OK (target=${TARGET})"