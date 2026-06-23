#!/usr/bin/env bash
# Phase 6 dogfood: share link → checkout → simulate pay → lab → studio order view
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"
DEMO_DIR="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"

echo "==> Health (billing + lab)"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
h=json.load(sys.stdin)
print('  status:', h['status'])
print('  billing:', h['checks'].get('billing', {}))
print('  lab:', h['checks'].get('lab', {}))
"

echo "==> Signup trial tenant"
STUDIO="phase6-$(date +%s)"
SLUG="p6-$(date +%s | tail -c 6)"
SIGNUP=$(curl -sf -X POST "$BASE/ui/saas/signup" \
  -d "studio_name=${STUDIO}&email=${SLUG}@dogfood.test&store_slug=${SLUG}")
API_KEY=$(echo "$SIGNUP" | grep -oE 'plutus_tk_[a-z0-9_-]+' | head -1)
test -n "$API_KEY"
echo "  tenant=$SLUG"

echo "==> Upload + analyze (1 photo)"
IMG=$(find "$DEMO_DIR" -maxdepth 1 -name '*.jpg' | sort | head -1)
test -n "$IMG"
UPLOAD_REDIRECT=$(curl -sf -X POST "$BASE/ui/saas/app/upload" \
  -H "Cookie: plutus_ui_token=${API_KEY}" \
  -F "gallery_name=Phase6 checkout demo" \
  -F "analyze=1" \
  -F "files=@${IMG}" \
  -D - -o /dev/null | grep -i '^location:' | awk '{print $2}' | tr -d '\r')
RUN_ID="${UPLOAD_REDIRECT#/runs/}"
echo "  run_id=$RUN_ID"

echo "==> Create share link"
LINK_JSON=$(curl -sf -X POST "$BASE/storefront/share-links" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "run_id=${RUN_ID}&label=Client+offer")
OFFER_URL=$(echo "$LINK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['public_url'])")
TOKEN=$(echo "$LINK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
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
load_dotenv("${ROOT}/.env", override=False)
from app import config, db
from app import orders as orders_mod
config.DATA_DIR = Path("${ROOT}/data")
config.DB_PATH = config.DATA_DIR / "plutus.db"
db.migrate()
prepared = orders_mod.prepare_bundle_order(
    tenant_id="${SLUG}",
    run_id=int("${RUN_ID}"),
    bundle_index=0,
    client_email="client@dogfood.test",
    client_name="Dogfood Client",
)
print(prepared["order_id"])
PY
)
echo "  order_id=$ORDER_ID"

if curl -sf "$BASE/healthz" | python3 -c "import json,sys; b=json.load(sys.stdin)['checks']['billing']; exit(0 if b.get('configured') else 1)" 2>/dev/null; then
  CHECKOUT_REDIRECT=$(curl -sf -X POST "${OFFER_URL}/checkout" \
    -d "bundle_index=0&client_email=stripe@dogfood.test&client_name=Stripe+Client" \
    -D - -o /dev/null | grep -i '^location:' | awk '{print $2}' | tr -d '\r' || true)
  echo "  stripe_checkout_url=$CHECKOUT_REDIRECT"
fi

echo "==> Simulate payment (test mode)"
PAY_JSON=$(curl -sf -X POST "$BASE/orders/${ORDER_ID}/simulate-payment" \
  -H "Authorization: Bearer ${API_KEY}")
echo "$PAY_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['status']=='paid', d
assert d.get('lab_status')=='submitted', d
print('  paid OK · lab', d.get('lab_ref'))
"

echo "==> Studio order detail"
ORDER_PAGE=$(curl -sL "$BASE/ui/saas/app/orders/${ORDER_ID}" \
  -H "Cookie: plutus_ui_token=${API_KEY}")
echo "$ORDER_PAGE" | grep -q "submitted" && echo "  order page shows lab status"

echo "==> Phase 6 dogfood OK — order #${ORDER_ID} · offer ${TOKEN:0:12}..."