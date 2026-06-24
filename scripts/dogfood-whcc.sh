#!/usr/bin/env bash
# WHCC lab dogfood on SaaS — stub submit/poll when API creds are absent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" 2>/dev/null || true

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"

echo "==> Wire WHCC adapter (stub when no WHCC_API_KEY)"
if [[ -n "${WHCC_API_KEY:-}" ]]; then
  bash "$ROOT/scripts/wire-whcc.sh"
else
  WHCC_STUB_ONLY=1 PLUTUS_ENV_FILE="$ENV_FILE" bash "$ROOT/scripts/wire-whcc.sh"
fi

echo "==> Health (lab adapter)"
curl -sf "$BASE/healthz" | python3 -c "
import json, sys
lab = json.load(sys.stdin)['checks']['lab']
print('  lab:', lab)
assert lab.get('adapter') == 'whcc', lab
assert lab.get('enabled'), lab
"

echo "==> Create paid order + WHCC stub fulfillment"
RESULT=$(ROOT="$ROOT" ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.environ["ROOT"])
os.chdir(os.environ["ROOT"])
from dotenv import load_dotenv

load_dotenv(os.environ["ENV_FILE"], override=True)
from app import config, db, lab, lab_whcc, tenants

config.LAB_MOCK_PROCESS_SECONDS = 0
config.LAB_MOCK_SHIP_SECONDS = 0
db.migrate()

slug = f"whcc-{int(datetime.now(UTC).timestamp()) % 100000}"
tenants.create_tenant(slug, name="WHCC Dogfood", store_slug=slug)
gid = db.insert_gallery(name="WHCC gallery", source="/x", photo_count=1, tenant_id=slug)
rid = db.insert_run(
    gallery_id=gid,
    engine="mock",
    bundle_count=1,
    estimated_total_cents=4500,
    payload={"bundles": []},
    tenant_id=slug,
)
oid = db.create_order(
    tenant_id=slug,
    run_id=rid,
    bundle_index=0,
    total_cents=4500,
    items=[{
        "sku": "print-8x10",
        "label": "8x10",
        "quantity": 1,
        "unit_cents": 4500,
        "image_url": "https://cdn.example/hero.jpg",
    }],
)
paid_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
db.update_order(oid, status="paid", paid_at=paid_at, client_email="whcc@dogfood.test")

submitted = lab.submit_order(oid)
assert submitted["lab_status"] == "submitted"
ref = submitted["lab_ref"]
assert ref

steps = 0
while steps < 8:
    polled = lab.poll_order(oid)
    steps += 1
    status = polled.get("lab_status")
    if status in {"complete", "skipped", "canceled"}:
        break
    if not polled.get("advanced"):
        break

order = db.get_order(oid)
status = order.get("lab_status")
if status not in {"shipped", "complete"}:
    body = {"order_id": ref, "status": "shipped"}
    import json

    payload = json.dumps(body).encode()
    secret = config.WHCC_WEBHOOK_SECRET or "whcc-dogfood-secret"
    sig = lab_whcc.whcc_webhook_signature(payload, secret=secret)
    lab_whcc.handle_webhook(body)
    order = db.get_order(oid)
    status = order.get("lab_status")

print(order["id"])
print(status)
print(order.get("client_token") or "")
print(ref)
PY
)

ORDER_ID=$(echo "$RESULT" | sed -n '1p')
LAB_STATUS=$(echo "$RESULT" | sed -n '2p')
TRACK_TOKEN=$(echo "$RESULT" | sed -n '3p')
LAB_REF=$(echo "$RESULT" | sed -n '4p')
echo "  order_id=$ORDER_ID lab_ref=$LAB_REF lab_status=$LAB_STATUS"

if [[ -z "$TRACK_TOKEN" ]]; then
  echo "order has no client track token" >&2
  exit 1
fi

TRACK_URL="${BASE}/store/order/track/${TRACK_TOKEN}"
echo "==> Client track page"
TRACK_PAGE=$(curl -sf "$TRACK_URL")
echo "$TRACK_PAGE" | grep -qi "$LAB_STATUS" && echo "  track page shows $LAB_STATUS"

if [[ "$LAB_STATUS" != "shipped" && "$LAB_STATUS" != "complete" ]]; then
  echo "expected lab to reach shipped or complete, got $LAB_STATUS" >&2
  exit 1
fi

echo "==> WHCC dogfood OK — order #${ORDER_ID} · ${LAB_STATUS}"