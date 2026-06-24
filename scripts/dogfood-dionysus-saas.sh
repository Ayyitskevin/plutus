#!/usr/bin/env bash
# SaaS Dionysus pitch: upload → recommend → pitch.txt with keyword enrichment (:8031).
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
BASE="http://${HOST}:${PORT}"
DEMO_DIR="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"

echo "==> Health (Dionysus on SaaS)"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
h=json.load(sys.stdin)
dio=h['checks'].get('dionysus') or {}
print('  dionysus:', dio)
assert dio.get('configured'), 'run scripts/wire-dionysus-saas.sh first'
assert dio.get('status') == 'ok', dio
"

echo "==> Signup trial tenant"
STUDIO="dio-$(date +%s)"
SLUG="d$(date +%s | tail -c 6)"
SIGNUP=$(curl -sf -X POST "$BASE/ui/saas/signup" \
  -d "studio_name=${STUDIO}&email=${SLUG}@dogfood.test&store_slug=${SLUG}")
API_KEY=$(echo "$SIGNUP" | grep -oE 'plutus_tk_[a-z0-9_-]+' | head -1)
test -n "$API_KEY"
echo "  tenant=$SLUG"

echo "==> Upload + analyze (1 photo)"
IMG=$(find "$DEMO_DIR" -maxdepth 1 -name '*.jpg' | sort | head -1)
test -n "$IMG"
UPLOAD_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/ui/saas/app/upload" \
  -H "Cookie: plutus_ui_token=${API_KEY}" \
  -F "gallery_name=Dionysus SaaS demo" \
  -F "analyze=1" \
  -F "files=@${IMG}")
if [[ "$UPLOAD_CODE" != "303" && "$UPLOAD_CODE" != "200" ]]; then
  echo "upload failed HTTP $UPLOAD_CODE" >&2
  exit 1
fi
BATCH_ID=$(python3 - <<PY
import os, sys
from pathlib import Path
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
test -n "$BATCH_ID"
RUN_JSON=$(curl -sf -X POST "$BASE/recommend/upload-batch" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "batch_id=${BATCH_ID}&sync=1")
RUN_ID=$(echo "$RUN_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
if d.get('queued'):
    raise SystemExit('batch still queued')
print(d['run_id'])
")
echo "  run_id=$RUN_ID"

echo "==> pitch.txt"
PITCH=$(curl -sf -H "Authorization: Bearer ${API_KEY}" "${BASE}/runs/${RUN_ID}/pitch.txt")
echo "$PITCH" | head -25
echo "$PITCH" | grep -qi "bundle\|▸" || { echo "pitch missing bundle section" >&2; exit 1; }
if ! echo "$PITCH" | grep -qi "Keywords that sell the story"; then
  echo "pitch missing Dionysus keyword enrichment" >&2
  exit 1
fi

echo "==> Dionysus SaaS dogfood OK — tenant ${SLUG} · run ${RUN_ID}"