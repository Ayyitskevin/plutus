#!/usr/bin/env bash
# Real Stripe test-mode checkout + signed webhook (no Stripe CLI required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
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

DEMO="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"
IMG=$(find "$DEMO" -maxdepth 1 -name '*.jpg' | head -1)
curl -sf -X POST "$BASE/ui/saas/app/upload" \
  -H "Cookie: plutus_ui_token=${API_KEY}" \
  -F "gallery_name=Stripe demo" \
  -F "files=@${IMG}" -o /dev/null
BATCH_ID=$(python3 - <<PY
import sqlite3
from pathlib import Path
con = sqlite3.connect("${ROOT}/data/plutus.db")
row = con.execute(
    "SELECT id FROM upload_batches WHERE tenant_id=? ORDER BY created_at DESC LIMIT 1",
    ("${SLUG}",),
).fetchone()
print(row[0] if row else "")
PY
)
test -n "$BATCH_ID"
RUN_JSON=$(curl -sf -X POST "$BASE/recommend/upload-batch" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "batch_id=${BATCH_ID}&sync=1")
RUN_ID=$(echo "$RUN_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])")
echo "  run_id=$RUN_ID"

echo "==> Share link + Stripe checkout session"
LINK=$(curl -sf -X POST "$BASE/storefront/share-links" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "run_id=${RUN_ID}")
OFFER=$(echo "$LINK" | python3 -c "import json,sys; print(json.load(sys.stdin)['public_url'])")
CHECKOUT_URL=$(curl -sf -X POST "${OFFER}/checkout" \
  -d "bundle_index=0&client_email=stripe-buyer@dogfood.test&client_name=Stripe+Buyer" \
  -D - -o /dev/null | grep -i '^location:' | awk '{print $2}' | tr -d '\r')
echo "  checkout_url=$CHECKOUT_URL"
echo "  Pay with test card 4242 4242 4242 4242 (any future date, any CVC)"

ORDER_ID=$(python3 - <<PY
import sqlite3
from pathlib import Path
con = sqlite3.connect("${ROOT}/data/plutus.db")
row = con.execute(
    "SELECT id, stripe_session_id FROM orders WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
    ("${SLUG}",),
).fetchone()
print(row[0])
open("/tmp/plutus_cs.txt","w").write(row[1] or "")
PY
)
SESSION_ID=$(cat /tmp/plutus_cs.txt)
echo "  order_id=$ORDER_ID session=$SESSION_ID"

echo "==> Simulate webhook (signed) — use after paying, or for CI without browser"
ROOT="${ROOT}" python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ROOT"])
from dotenv import load_dotenv
load_dotenv(".env")
from app import billing, config
config.DATA_DIR = Path("data")
config.DB_PATH = config.DATA_DIR / "plutus.db"
order_id = sys.argv[1]
session_id = sys.argv[2]
tenant_id = sys.argv[3]
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
"$ORDER_ID" "$SESSION_ID" "$SLUG"

echo "==> Stripe dogfood OK — open checkout URL to pay for real, or webhook already simulated"