#!/usr/bin/env bash
# Real Stripe test-mode checkout + signed webhook (no Stripe CLI required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-session.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-wait-batch.sh"
source .venv/bin/activate 2>/dev/null || true

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"

if [[ -z "${STRIPE_SECRET_KEY:-}" ]] && [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${STRIPE_SECRET_KEY:-}" ]] || [[ "${STRIPE_SECRET_KEY}" == *CHANGE_ME* ]]; then
  echo "Set STRIPE_SECRET_KEY=sk_test_... in .env (from Stripe Dashboard → Developers → API keys)" >&2
  exit 1
fi
if [[ -z "${STRIPE_WEBHOOK_SECRET:-}" ]] || [[ "${STRIPE_WEBHOOK_SECRET}" == *CHANGE_ME* ]]; then
  echo "Set STRIPE_WEBHOOK_SECRET=whsec_... in .env" >&2
  echo "  Run: bash scripts/stripe-listen.sh   (copy whsec from output)" >&2
  echo "  Or use a fixed test secret for local signed-webhook dogfood only." >&2
  exit 1
fi

echo "==> Stripe connectivity"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
b=json.load(sys.stdin)['checks']['billing']
print('  billing:', b)
assert b.get('reachable'), 'Stripe API unreachable — check STRIPE_SECRET_KEY'
"

echo "==> Signup + quick run"
STUDIO="stripe-$(date +%s)"
SLUG="st-$(date +%s | tail -c 6)"
SIGNUP=$(curl -sf -X POST "$BASE/ui/saas/signup" \
  -d "studio_name=${STUDIO}&email=${SLUG}@dogfood.test&store_slug=${SLUG}")
API_KEY=$(echo "$SIGNUP" | grep -oE 'plutus_tk_[a-z0-9_-]+' | head -1)
test -n "$API_KEY"
dogfood_session_login "$BASE" "$API_KEY"

DEMO="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"
IMG=$(find "$DEMO" -maxdepth 1 -name '*.jpg' | head -1)
UPLOAD_CODE=$(dogfood_ui_post -s -o /dev/null -w "%{http_code}" -X POST "$BASE/ui/saas/app/upload" \
  -F "gallery_name=Stripe demo" \
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
load_dotenv("${ROOT}/.env", override=True)
from app import config, db
db.migrate()
rows = db.list_upload_batches(tenant_id="${SLUG}", limit=1)
print(rows[0]["id"] if rows else "")
PY
)
test -n "$BATCH_ID"
dogfood_wait_batch "$BASE" "$API_KEY" "$BATCH_ID"
RUN_ID="$DOGFOOD_RUN_ID"

echo "==> Share link + Stripe checkout session"
LINK=$(curl -sf -X POST "$BASE/storefront/share-links" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "run_id=${RUN_ID}")
OFFER=$(echo "$LINK" | python3 -c "import json,sys; print(json.load(sys.stdin)['public_url'])")
CHECKOUT_URL=$(curl -sf -X POST "${OFFER}/checkout" \
  -d "bundle_index=0&client_email=stripe-buyer@dogfood.test&client_name=Stripe+Buyer" \
  -D - -o /dev/null | grep -i '^location:' | awk '{print $2}' | tr -d '\r')
echo "  checkout_url=$CHECKOUT_URL"
if curl -sf "$BASE/saas/billing/status" | python3 -c "import json,sys; exit(0 if json.load(sys.stdin).get('test_mode') else 1)" 2>/dev/null; then
  echo "  Pay with test card 4242 4242 4242 4242 (any future date, any CVC)"
else
  echo "  LIVE Stripe checkout — real card required (or use simulated webhook below)"
fi

ORDER_ID=$(python3 - <<PY
import os, sys
from pathlib import Path
sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
from dotenv import load_dotenv
load_dotenv("${ROOT}/.env", override=True)
from app import db
db.migrate()
rows = db.list_orders(tenant_id="${SLUG}", limit=1)
row = rows[0] if rows else {}
print(row.get("id", ""))
open("/tmp/plutus_cs.txt", "w").write(row.get("stripe_session_id") or "")
PY
)
SESSION_ID=$(cat /tmp/plutus_cs.txt)
echo "  order_id=$ORDER_ID session=$SESSION_ID"

echo "==> Simulate webhook (signed) — use after paying, or for CI without browser"
ROOT="${ROOT}" ORDER_ID="${ORDER_ID}" SESSION_ID="${SESSION_ID}" TENANT_ID="${SLUG}" python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ROOT"])
from dotenv import load_dotenv
load_dotenv(".env")
from app import billing, config, db
db.migrate()
order_id = os.environ["ORDER_ID"]
session_id = os.environ["SESSION_ID"]
tenant_id = os.environ["TENANT_ID"]
event = {
    "id": f"evt_dogfood_{order_id}",
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id": session_id,
            "metadata": {
                "order_id": order_id,
                "tenant_id": tenant_id,
                "checkout_kind": "client_bundle",
            },
            "customer_details": {"email": "stripe-buyer@dogfood.test"},
            "payment_intent": f"pi_dogfood_{order_id}",
        }
    },
}
payload = json.dumps(event).encode()
sig = billing.sign_webhook_payload(payload)
import httpx
base = f"http://{os.environ.get('PLUTUS_HOST', '127.0.0.1')}:{os.environ.get('PLUTUS_PORT', '8031')}"
r = httpx.post(f"{base}/webhooks/stripe", content=payload, headers={"stripe-signature": sig})
print("  webhook HTTP", r.status_code, r.json())
PY

echo "==> Stripe dogfood OK — open checkout URL to pay for real, or webhook already simulated"