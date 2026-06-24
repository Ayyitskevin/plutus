#!/usr/bin/env bash
# Phase 6 dogfood: share link → checkout → pay → lab → studio order view
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
DEMO_DIR="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"

echo "==> Health (billing + lab)"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
h=json.load(sys.stdin)
print('  status:', h['status'])
print('  billing:', h['checks'].get('billing', {}))
print('  lab:', h['checks'].get('lab', {}))
"

PLUTUS_DOGFOOD_ROOT="$ROOT"
echo "==> Dogfood tenant"
SLUG="p6-$(date +%s | tail -c 6)"
dogfood_bootstrap_tenant "$SLUG" "Phase6 Studio"
API_KEY="$PLUTUS_DOGFOOD_API_KEY"
test -n "$API_KEY"
echo "  tenant=$SLUG"
dogfood_session_login "$BASE" "$API_KEY"

echo "==> Upload + analyze (1 photo)"
IMG=$(find "$DEMO_DIR" -maxdepth 1 -name '*.jpg' | sort | head -1)
test -n "$IMG"
UPLOAD_CODE=$(dogfood_ui_post -s -o /dev/null -w "%{http_code}" -X POST "$BASE/ui/saas/app/upload" \
  -F "gallery_name=Phase6 checkout demo" \
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
dogfood_wait_batch "$BASE" "$API_KEY" "$BATCH_ID"
RUN_ID="$DOGFOOD_RUN_ID"

echo "==> Create share link"
LINK_JSON=$(curl -sf -X POST "$BASE/storefront/share-links" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "run_id=${RUN_ID}&label=Client+offer")
OFFER_URL=$(echo "$LINK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['public_url'])")
TOKEN=$(echo "$LINK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
echo "  offer=$OFFER_URL"

echo "==> Client views offer"
curl -sf "$OFFER_URL" | grep -q "Buy this package" && echo "  storefront OK"

echo "==> Checkout session (bundle 0)"
CHECKOUT_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${OFFER_URL}/checkout" \
  -d "bundle_index=0&client_email=client@dogfood.test&client_name=Dogfood+Client" \
  -D /tmp/plutus-phase6-checkout.hdr)
if [[ "$CHECKOUT_CODE" != "303" && "$CHECKOUT_CODE" != "200" ]]; then
  echo "checkout failed HTTP $CHECKOUT_CODE" >&2
  exit 1
fi
grep -qi '^location:' /tmp/plutus-phase6-checkout.hdr && \
  echo "  checkout_url=$(grep -i '^location:' /tmp/plutus-phase6-checkout.hdr | awk '{print $2}' | tr -d '\r')"

ORDER_ID=$(python3 - <<PY
import os, sys
from pathlib import Path
sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
from dotenv import load_dotenv
load_dotenv("${ENV_FILE}", override=True)
from app import db
db.migrate()
rows = db.list_orders(tenant_id="${SLUG}", limit=1)
row = rows[0] if rows else {}
print(row.get("id", ""))
Path("/tmp/plutus_phase6_cs.txt").write_text(row.get("stripe_session_id") or "")
PY
)
SESSION_ID=$(cat /tmp/plutus_phase6_cs.txt 2>/dev/null || true)
test -n "$ORDER_ID"
echo "  order_id=$ORDER_ID session=$SESSION_ID"

if curl -sf "$BASE/healthz" | python3 -c "import json,sys; exit(0 if json.load(sys.stdin)['checks']['billing'].get('test_mode') else 1)" 2>/dev/null; then
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
else
  echo "==> Signed webhook (live Stripe — simulate disabled)"
  ROOT="${ROOT}" ORDER_ID="${ORDER_ID}" SESSION_ID="${SESSION_ID}" TENANT_ID="${SLUG}" \
    PLUTUS_ENV_FILE="${ENV_FILE}" PLUTUS_HOST="${HOST}" PLUTUS_PORT="${PORT}" python3 - <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["ROOT"])
from dotenv import load_dotenv
load_dotenv(os.environ.get("PLUTUS_ENV_FILE", ".env"), override=True)
from app import billing, db
db.migrate()
event = {
    "id": f"evt_phase6_{os.environ['ORDER_ID']}",
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id": os.environ["SESSION_ID"],
            "metadata": {
                "order_id": os.environ["ORDER_ID"],
                "tenant_id": os.environ["TENANT_ID"],
                "checkout_kind": "client_bundle",
            },
            "customer_details": {"email": "client@dogfood.test"},
            "payment_intent": f"pi_phase6_{os.environ['ORDER_ID']}",
        }
    },
}
payload = json.dumps(event).encode()
sig = billing.sign_webhook_payload(payload)
import httpx
base = f"http://{os.environ.get('PLUTUS_HOST', '127.0.0.1')}:{os.environ.get('PLUTUS_PORT', '8031')}"
r = httpx.post(f"{base}/webhooks/stripe", content=payload, headers={"stripe-signature": sig})
print("  webhook HTTP", r.status_code, r.json())
assert r.status_code == 200, r.text
PY
  python3 - <<PY
import os, sys
from pathlib import Path
sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
from dotenv import load_dotenv
load_dotenv("${ENV_FILE}", override=True)
from app import db
db.migrate()
order = db.get_order(int("${ORDER_ID}"), tenant_id="${SLUG}")
assert order and order.get("status") == "paid", order
assert order.get("lab_status") == "submitted", order
print("  paid OK · lab", order.get("lab_ref"))
PY
fi

echo "==> Studio order detail"
ORDER_PAGE=$(dogfood_ui_get -sL "$BASE/ui/saas/app/orders/${ORDER_ID}")
echo "$ORDER_PAGE" | grep -q "submitted" && echo "  order page shows lab status"

echo "==> Phase 6 dogfood OK — order #${ORDER_ID} · offer ${TOKEN:0:12}..."