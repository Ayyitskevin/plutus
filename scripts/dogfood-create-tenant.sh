#!/usr/bin/env bash
# Create a dogfood tenant + API key (signup is closed by default).
# Prints: PLUTUS_DOGFOOD_TENANT_ID=... PLUTUS_DOGFOOD_API_KEY=...
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

TENANT_ID="${1:-dogfood-$(date +%s | tail -c 8)}"
NAME="${2:-Dogfood Studio}"
SLUG="${3:-$TENANT_ID}"

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" 2>/dev/null || true
python3 - <<PY
import os
import sys
from pathlib import Path

sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
from dotenv import load_dotenv

load_dotenv("${ENV_FILE}", override=True)
from app import db, tenants

db.migrate()
tid = "${TENANT_ID}"
if db.get_tenant(tid):
    print(f"tenant exists: {tid}", file=sys.stderr)
else:
    tenants.create_tenant(tid, name="${NAME}", store_slug="${SLUG}")
    from datetime import UTC, datetime

    db.update_tenant(tid, email_verified_at=datetime.now(UTC).isoformat())
issued = tenants.issue_api_key(tid, label="dogfood")
print(f"PLUTUS_DOGFOOD_TENANT_ID={tid}")
print(f"PLUTUS_DOGFOOD_API_KEY={issued['api_key']}")
PY