#!/usr/bin/env bash
# Dogfood tenant Pro subscription — signup → signed subscription webhook → active billing
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .venv/bin/activate 2>/dev/null || true

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"

if [[ -z "${STRIPE_WEBHOOK_SECRET:-}" ]] || [[ "${STRIPE_WEBHOOK_SECRET}" == *CHANGE_ME* ]]; then
  echo "Set STRIPE_WEBHOOK_SECRET in .env (stripe listen or dogfood-stripe-real whsec)" >&2
  exit 1
fi

echo "==> Billing configured"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
b=json.load(sys.stdin)['checks']['billing']
print('  billing:', b)
assert b.get('configured') and b.get('reachable'), 'Stripe not ready'
"

SLUG="sb-$(date +%s | tail -c 6)"
PLUTUS_DOGFOOD_ROOT="$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-session.sh"
echo "==> Dogfood tenant $SLUG"
dogfood_bootstrap_tenant "$SLUG" "Subscription Studio"
API_KEY="$PLUTUS_DOGFOOD_API_KEY"
test -n "$API_KEY"

echo "==> Simulate subscription webhook (checkout.session.completed)"
ROOT="${ROOT}" TENANT_ID="${SLUG}" python3 - <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["ROOT"])
from dotenv import load_dotenv
load_dotenv(".env")
from app import billing, db
db.migrate()
tenant_id = os.environ["TENANT_ID"]
tenant = db.get_tenant(tenant_id)
assert tenant, tenant_id
event = {
    "id": f"evt_sub_{tenant_id}",
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id": f"cs_sub_{tenant_id}",
            "customer": f"cus_dogfood_{tenant_id}",
            "subscription": f"sub_dogfood_{tenant_id}",
            "metadata": {
                "tenant_id": tenant_id,
                "checkout_kind": "tenant_subscription",
            },
        }
    },
}
payload = json.dumps(event).encode()
sig = billing.sign_webhook_payload(payload)
import httpx
base = f"http://{os.environ.get('PLUTUS_HOST', '127.0.0.1')}:{os.environ.get('PLUTUS_PORT', '8031')}"
r = httpx.post(f"{base}/webhooks/stripe", content=payload, headers={"stripe-signature": sig})
print("  webhook HTTP", r.status_code, r.json())
assert r.status_code == 200
updated = db.get_tenant(tenant_id)
print("  billing_status:", updated.get("billing_status"))
print("  plan_tier:", updated.get("plan_tier"))
print("  cap:", updated.get("monthly_recommend_cap"))
assert updated.get("billing_status") == "active"
assert updated.get("plan_tier") == "pro"
PY

echo "==> Subscription dogfood OK — tenant $SLUG is Pro"