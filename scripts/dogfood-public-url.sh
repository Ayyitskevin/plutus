#!/usr/bin/env bash
# Verify share links and track URLs use PLUTUS_SAAS_PUBLIC_URL.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-session.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-wait-batch.sh"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"
PUBLIC_BASE="${PLUTUS_SAAS_PUBLIC_URL:?PLUTUS_SAAS_PUBLIC_URL required}"
export PUBLIC_BASE
DEMO_DIR="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"

echo "==> Public base: $PUBLIC_BASE"

STUDIO="pub-$(date +%s)"
SLUG="u$(date +%s | tail -c 6)"
SIGNUP=$(curl -sf -X POST "$BASE/ui/saas/signup" \
  -d "studio_name=${STUDIO}&email=${SLUG}@dogfood.test&store_slug=${SLUG}")
API_KEY=$(echo "$SIGNUP" | grep -oE 'plutus_tk_[a-z0-9_-]+' | head -1)
test -n "$API_KEY"
dogfood_session_login "$BASE" "$API_KEY"

IMG=$(find "$DEMO_DIR" -maxdepth 1 -name '*.jpg' | sort | head -1)
test -n "$IMG"
dogfood_ui_post -sf -X POST "$BASE/ui/saas/app/upload" \
  -F "gallery_name=Public URL demo" \
  -F "files=@${IMG}" >/dev/null

BATCH_ID=$(python3 - <<PY
import os, sys
sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
from dotenv import load_dotenv
load_dotenv("${ENV_FILE}", override=True)
from app import db
db.migrate()
rows = db.list_upload_batches(tenant_id="${SLUG}", limit=1)
print(rows[0]["id"] if rows else "")
PY
)
dogfood_wait_batch "$BASE" "$API_KEY" "$BATCH_ID"
RUN_ID="$DOGFOOD_RUN_ID"

LINK_JSON=$(curl -sf -X POST "$BASE/storefront/share-links" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "run_id=${RUN_ID}&label=Public+smoke")
echo "$LINK_JSON" | python3 -c "
import json, sys, os
body = json.load(sys.stdin)
base = os.environ['PUBLIC_BASE'].rstrip('/')
url = body['public_url']
assert url.startswith(base + '/'), f'expected {base}/… got {url}'
print('  offer_url OK:', url)
" 

echo "==> Public URL dogfood OK — base ${PUBLIC_BASE}"