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

if [[ -z "${STRIPE_SECRET_KEY:-}" ]] && [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Bind address in .env (0.0.0.0) is for the server — dogfood curls hit loopback.
HOST="127.0.0.1"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"

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

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
RATE_LIMIT_WAS=""
COOKIE_SECURE_WAS=""
if [[ -f "$ENV_FILE" ]] && grep -q '^PLUTUS_RATE_LIMIT_ENABLED=' "$ENV_FILE"; then
  RATE_LIMIT_WAS="$(grep '^PLUTUS_RATE_LIMIT_ENABLED=' "$ENV_FILE" | tail -1)"
fi
if [[ -f "$ENV_FILE" ]] && grep -q '^PLUTUS_UI_COOKIE_SECURE=' "$ENV_FILE"; then
  COOKIE_SECURE_WAS="$(grep '^PLUTUS_UI_COOKIE_SECURE=' "$ENV_FILE" | tail -1)"
fi
restore_dogfood_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi
  if [[ -n "$RATE_LIMIT_WAS" ]]; then
    sed -i "s|^PLUTUS_RATE_LIMIT_ENABLED=.*|${RATE_LIMIT_WAS}|" "$ENV_FILE"
  fi
  if [[ -n "$COOKIE_SECURE_WAS" ]]; then
    sed -i "s|^PLUTUS_UI_COOKIE_SECURE=.*|${COOKIE_SECURE_WAS}|" "$ENV_FILE"
  elif grep -q '^PLUTUS_UI_COOKIE_SECURE=' "$ENV_FILE"; then
    sed -i '/^PLUTUS_UI_COOKIE_SECURE=/d' "$ENV_FILE"
  fi
  if systemctl --user is-active plutus-saas >/dev/null 2>&1; then
    systemctl --user restart plutus-saas
  fi
}
trap restore_dogfood_env EXIT

if [[ -f "$ENV_FILE" ]]; then
  if grep -q '^PLUTUS_RATE_LIMIT_ENABLED=' "$ENV_FILE"; then
    sed -i 's/^PLUTUS_RATE_LIMIT_ENABLED=.*/PLUTUS_RATE_LIMIT_ENABLED=false/' "$ENV_FILE"
  else
    echo "PLUTUS_RATE_LIMIT_ENABLED=false" >>"$ENV_FILE"
  fi
  if grep -q '^PLUTUS_UI_COOKIE_SECURE=' "$ENV_FILE"; then
    sed -i 's/^PLUTUS_UI_COOKIE_SECURE=.*/PLUTUS_UI_COOKIE_SECURE=false/' "$ENV_FILE"
  else
    echo "PLUTUS_UI_COOKIE_SECURE=false" >>"$ENV_FILE"
  fi
  if systemctl --user is-active plutus-saas >/dev/null 2>&1; then
    echo "==> Restart plutus-saas (dogfood rate-limit bypass)"
    systemctl --user restart plutus-saas
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if curl -sf "http://${HOST}:${PORT}/healthz" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  fi
fi

echo "==> Stripe connectivity"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
b=json.load(sys.stdin)['checks']['billing']
print('  billing:', b)
assert b.get('reachable'), 'Stripe API unreachable — check STRIPE_SECRET_KEY'
"

PLUTUS_DOGFOOD_ROOT="$ROOT"
echo "==> Dogfood tenant"
SLUG="st-$(date +%s | tail -c 6)"
dogfood_bootstrap_tenant "$SLUG" "Stripe Studio"
API_KEY="$PLUTUS_DOGFOOD_API_KEY"
test -n "$API_KEY"
dogfood_session_login "$BASE" "$API_KEY"

DEMO="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"
IMG=$(find "$DEMO" -maxdepth 1 -name '*.jpg' | head -1)
UPLOAD_CODE=$(dogfood_ui_post -s -o /dev/null -w "%{http_code}" -X POST "$BASE/ui/saas/app/upload" \
  -F "gallery_name=Stripe demo" \
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
load_dotenv("${ROOT}/.env", override=True)
from app import config, db
db.migrate()
rows = db.list_upload_batches(tenant_id="${PLUTUS_DOGFOOD_TENANT_ID}", limit=1)
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
LOCAL_OFFER=$(python3 - <<PY
public = """${OFFER}"""
public_base = "${PLUTUS_SAAS_PUBLIC_URL:-}".rstrip("/")
local_base = "${BASE}"
if public_base and public.startswith(public_base):
    print(public.replace(public_base, local_base, 1))
else:
    print(public)
PY
)
CHECKOUT_URL=$(curl -s -X POST "${LOCAL_OFFER}/checkout" \
  -d "bundle_index=0&client_email=stripe-buyer@dogfood.test&client_name=Stripe+Buyer" \
  -D - -o /dev/null | grep -i '^location:' | awk '{print $2}' | tr -d '\r' || true)
echo "  checkout_url=${CHECKOUT_URL:-(skipped)}"
if [[ -n "$CHECKOUT_URL" ]] && curl -sf "$BASE/saas/billing/status" | python3 -c "import json,sys; exit(0 if json.load(sys.stdin).get('test_mode') else 1)" 2>/dev/null; then
  echo "  Pay with test card 4242 4242 4242 4242 (any future date, any CVC)"
elif [[ -n "$CHECKOUT_URL" ]]; then
  echo "  LIVE Stripe checkout — real card required (or use simulated webhook below)"
else
  echo "  Checkout skipped (live Stripe disabled) — creating pending order for webhook dogfood"
fi

ORDER_INFO=$(ROOT="$ROOT" RUN_ID="$RUN_ID" TENANT_ID="$PLUTUS_DOGFOOD_TENANT_ID" python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ["ROOT"])
os.chdir(os.environ["ROOT"])
from dotenv import load_dotenv
load_dotenv(".env", override=True)
from app import db, orders
db.migrate()
tenant_id = os.environ["TENANT_ID"]
run_id = int(os.environ["RUN_ID"])
rows = db.list_orders(tenant_id=tenant_id, limit=1)
if rows:
    row = rows[0]
else:
    prepared = orders.prepare_bundle_order(
        tenant_id=tenant_id,
        run_id=run_id,
        bundle_index=0,
        client_email="stripe-buyer@dogfood.test",
        client_name="Stripe Buyer",
    )
    row = db.get_order(prepared["order_id"]) or {}
oid = row.get("id", "")
session = row.get("stripe_session_id") or f"cs_dogfood_{oid}"
print(oid)
print(session)
PY
)
ORDER_ID=$(echo "$ORDER_INFO" | sed -n '1p')
SESSION_ID=$(echo "$ORDER_INFO" | sed -n '2p')
test -n "$ORDER_ID"
echo "  order_id=$ORDER_ID session=$SESSION_ID"

echo "==> Simulate webhook (signed) — use after paying, or for CI without browser"
ROOT="${ROOT}" ORDER_ID="${ORDER_ID}" SESSION_ID="${SESSION_ID}" TENANT_ID="${PLUTUS_DOGFOOD_TENANT_ID}" python3 - <<'PY'
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