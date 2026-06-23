#!/usr/bin/env bash
# Homelab money loop: share link → offer → simulate pay → lab → order view (:8030)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8030}"
BASE="http://${HOST}:${PORT}"
TOKEN="${PLUTUS_API_TOKEN:?PLUTUS_API_TOKEN required}"
RUN_ID="${PLUTUS_DOGFOOD_RUN_ID:-5}"

echo "==> Health (homelab store + billing + lab)"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
h=json.load(sys.stdin)
print('  status:', h['status'])
print('  homelab_store:', h.get('homelab_store'))
print('  billing:', h['checks'].get('billing', {}))
print('  lab:', h['checks'].get('lab', {}))
assert h.get('homelab_store'), 'homelab storefront not enabled'
assert h['checks'].get('lab', {}).get('enabled'), 'lab adapter disabled'
"

echo "==> Verify run #${RUN_ID}"
curl -sf "$BASE/runs/${RUN_ID}/json" | python3 -c "
import json,sys
r=json.load(sys.stdin)
assert r.get('id')==int('${RUN_ID}'), r
bundles=(r.get('payload') or {}).get('bundles') or []
assert bundles, 'run has no bundles'
print('  bundles:', len(bundles))
"

echo "==> Create share link (admin bearer)"
LINK_JSON=$(curl -sf -X POST "$BASE/storefront/share-links" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d "run_id=${RUN_ID}&label=Homelab+client+offer")
OFFER_URL=$(echo "$LINK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['public_url'])")
TOKEN_SLUG=$(echo "$LINK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
echo "  offer=$OFFER_URL"

echo "==> Client views offer"
curl -sf "$OFFER_URL" | grep -q "Buy this package" && echo "  storefront OK"

echo "==> Create pending order (bundle 0)"
ORDER_ID=$(cd "$ROOT" && source .venv/bin/activate 2>/dev/null || true
python3 - <<PY
import os, sys
from pathlib import Path
sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
from dotenv import load_dotenv
load_dotenv("${ENV_FILE}", override=True)
from app import config, db
from app import orders as orders_mod
db.migrate()
prepared = orders_mod.prepare_bundle_order(
    tenant_id=config.HOMELAB_TENANT_ID,
    run_id=int("${RUN_ID}"),
    bundle_index=0,
    client_email="client@dogfood.test",
    client_name="Homelab Client",
)
print(prepared["order_id"])
PY
)
echo "  order_id=$ORDER_ID"

echo "==> Simulate payment"
PAY_JSON=$(curl -sf -X POST "$BASE/orders/${ORDER_ID}/simulate-payment" \
  -H "Authorization: Bearer ${TOKEN}")
TRACK_URL=$(echo "$PAY_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['status']=='paid', d
assert d.get('lab_status')=='submitted', d
url=d.get('client_track_url') or ''
assert url, 'missing client_track_url'
print('  paid OK · lab', d.get('lab_ref'), file=sys.stderr)
print(url)
")
echo "  track=$TRACK_URL"

echo "==> Homelab order detail"
ORDER_PAGE=$(curl -sL "$BASE/ui/homelab/orders/${ORDER_ID}")
echo "$ORDER_PAGE" | grep -q "submitted" && echo "  order page shows lab status"

echo "==> Public client track page (no auth)"
TRACK_PAGE=$(curl -sf "$TRACK_URL")
echo "$TRACK_PAGE" | grep -q "submitted" && echo "  client track page OK"

echo "==> Homelab money dogfood OK — order #${ORDER_ID} · offer ${TOKEN_SLUG:0:12}..."