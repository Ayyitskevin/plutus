#!/usr/bin/env bash
# Verify admin-token /integrations/offer works for mnemosyne / integration callers.
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

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
# Dogfood hits the local process; public_url in the JSON still uses PLUTUS_SAAS_PUBLIC_URL.
BASE="http://${HOST}:${PORT}"
TOKEN="${PLUTUS_API_TOKEN:?Set PLUTUS_API_TOKEN (admin)}"
TENANT_ID="${PLUTUS_MISE_HOOK_TENANT_ID:-${MNEMOSYNE_PLUTUS_TENANT_ID:-flow-studio}}"
RUN_ID="${1:-}"

if [[ -z "$RUN_ID" ]]; then
  RUN_ID=$(python3 - <<PY
import os, sys
sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
from dotenv import load_dotenv
load_dotenv("${ENV_FILE}", override=True)
from app import db
db.migrate()
with db.connection() as con:
    row = con.execute(
        "SELECT id FROM recommendation_runs WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
        ("${TENANT_ID}",),
    ).fetchone()
print(row["id"] if row else "")
PY
)
fi
if [[ -z "$RUN_ID" ]]; then
  echo "no run for tenant ${TENANT_ID} — upload a gallery first" >&2
  exit 1
fi

echo "==> Mint offer link (admin + tenant_id=${TENANT_ID}, run_id=${RUN_ID})"
LINK_JSON=$(curl -sf -X POST "$BASE/integrations/offer" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d "run_id=${RUN_ID}&tenant_id=${TENANT_ID}&label=Mnemosyne+integration+test")
OFFER_URL=$(echo "$LINK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['public_url'])")
echo "  offer=$OFFER_URL"

echo "==> Client can view offer"
curl -sf "$OFFER_URL" | grep -qi "package\|bundle\|buy" && echo "  storefront OK"

echo "Done — mnemosyne should set MNEMOSYNE_PLUTUS_TENANT_ID=${TENANT_ID}"