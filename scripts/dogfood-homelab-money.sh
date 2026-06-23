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

echo "==> Checkout session (bundle 0)"
CHECKOUT_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${OFFER_URL}/checkout" \
  -d "bundle_index=0&client_email=client@dogfood.test&client_name=Homelab+Client" \
  -D /tmp/plutus-homelab-checkout.hdr)
if [[ "$CHECKOUT_CODE" != "303" && "$CHECKOUT_CODE" != "200" ]]; then
  echo "checkout failed HTTP $CHECKOUT_CODE" >&2
  exit 1
fi
grep -qi '^location:' /tmp/plutus-homelab-checkout.hdr && echo "  checkout_url=$(grep -i '^location:' /tmp/plutus-homelab-checkout.hdr | awk '{print $2}' | tr -d '\r')"

DATA_DIR="${PLUTUS_DATA_DIR:-$ROOT/data-homelab}"
ORDER_ID=$(python3 - <<PY
import sqlite3
from pathlib import Path
db_path = Path("${DATA_DIR}") / "plutus.db"
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row
row = con.execute(
    "SELECT id, stripe_session_id FROM orders WHERE tenant_id='homelab' ORDER BY id DESC LIMIT 1"
).fetchone()
if row:
    print(row["id"])
    Path("/tmp/plutus_homelab_cs.txt").write_text(row["stripe_session_id"] or "")
PY
)
SESSION_ID=$(cat /tmp/plutus_homelab_cs.txt 2>/dev/null || true)
test -n "$ORDER_ID"
echo "  order_id=$ORDER_ID session=$SESSION_ID"

if curl -sf "$BASE/healthz" | python3 -c "import json,sys; exit(0 if json.load(sys.stdin)['checks']['billing'].get('test_mode') else 1)" 2>/dev/null; then
  echo "==> Simulate payment (test mode)"
  PAY_JSON=$(curl -sf -X POST "$BASE/orders/${ORDER_ID}/simulate-payment" \
    -H "Authorization: Bearer ${TOKEN}")
  TRACK_URL=$(echo "$PAY_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['status']=='paid', d
url=d.get('client_track_url') or ''
assert url, 'missing client_track_url'
print(url)
")
else
  echo "==> Signed webhook (live Stripe — simulate disabled)"
  ROOT="${ROOT}" ORDER_ID="${ORDER_ID}" SESSION_ID="${SESSION_ID}" TENANT_ID="studio" python3 - <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["ROOT"])
os.environ.pop("PLUTUS_DATABASE_URL", None)
from dotenv import load_dotenv
load_dotenv(os.environ.get("PLUTUS_ENV_FILE", ".env.homelab"), override=True)
from app import billing, db
db.migrate()
event = {
    "id": f"evt_homelab_{os.environ['ORDER_ID']}",
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id": os.environ["SESSION_ID"],
            "metadata": {
                "order_id": os.environ["ORDER_ID"],
                "tenant_id": "homelab",
                "checkout_kind": "client_bundle",
            },
            "customer_details": {"email": "client@dogfood.test"},
            "payment_intent": f"pi_homelab_{os.environ['ORDER_ID']}",
        }
    },
}
payload = json.dumps(event).encode()
sig = billing.sign_webhook_payload(payload)
import httpx
base = f"http://{os.environ.get('PLUTUS_HOST', '127.0.0.1')}:{os.environ.get('PLUTUS_PORT', '8030')}"
r = httpx.post(f"{base}/webhooks/stripe", content=payload, headers={"stripe-signature": sig})
print("  webhook HTTP", r.status_code, r.json())
assert r.status_code == 200, r.text
PY
  TRACK_URL=$(python3 - <<PY
import sqlite3
from pathlib import Path
db_path = Path("${DATA_DIR}") / "plutus.db"
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row
row = con.execute("SELECT client_token FROM orders WHERE id=?", (int("${ORDER_ID}"),)).fetchone()
print(f"http://127.0.0.1:8030/store/order/track/{row['client_token']}")
PY
)
fi
echo "  track=$TRACK_URL"

echo "==> Homelab order detail"
ORDER_PAGE=$(curl -sL "$BASE/ui/homelab/orders/${ORDER_ID}")
echo "$ORDER_PAGE" | grep -q "submitted" && echo "  order page shows lab status"

echo "==> Public client track page (no auth)"
TRACK_PAGE=$(curl -sf "$TRACK_URL")
echo "$TRACK_PAGE" | grep -q "submitted" && echo "  client track page OK"

echo "==> Homelab money dogfood OK — order #${ORDER_ID} · offer ${TOKEN_SLUG:0:12}..."