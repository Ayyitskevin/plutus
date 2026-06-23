#!/usr/bin/env bash
# Dogfood order-paid webhook notifications (local catcher on :9999).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .venv/bin/activate 2>/dev/null || true

PORT="${PLUTUS_NOTIFY_PORT:-9999}"
WEBHOOK_URL="http://127.0.0.1:${PORT}/plutus-events"
CATCHER_PID=""

cleanup() {
  if [[ -n "$CATCHER_PID" ]] && kill -0 "$CATCHER_PID" 2>/dev/null; then
    kill "$CATCHER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

python3 "$ROOT/scripts/notify-webhook-catcher.py" "$PORT" &
CATCHER_PID=$!
sleep 0.5

echo "==> Fire notify_order_paid with PLUTUS_ORDER_WEBHOOK_URL=$WEBHOOK_URL"
PLUTUS_ORDER_WEBHOOK_URL="$WEBHOOK_URL" python3 - <<'PY'
import os
os.environ.setdefault("PLUTUS_DATABASE_URL", "")
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PLUTUS_ORDER_WEBHOOK_URL"] = os.environ.get("PLUTUS_ORDER_WEBHOOK_URL", "")
from app import config, db, notifications, tenants

db.migrate()
tid = "notifyco"
if not db.get_tenant(tid):
    tenants.create_tenant(tid, name="Notify Co", store_slug="notify-co")
gid = db.insert_gallery(name="G", source="/x", photo_count=1, tenant_id=tid)
rid = db.insert_run(
    gallery_id=gid,
    engine="mock",
    bundle_count=1,
    estimated_total_cents=1200,
    payload={"bundles": [{"title": "A", "items": [{"sku": "print-8x10", "label": "Print", "unit_cents": 1200, "quantity": 1, "photo": "01.jpg"}]}]},
    tenant_id=tid,
)
from app import orders as orders_mod
prepared = orders_mod.prepare_bundle_order(
    tenant_id=tid,
    run_id=rid,
    bundle_index=0,
    client_email="buyer@dogfood.test",
    client_name="Buyer",
)
oid = prepared["order_id"]
result = orders_mod.mark_order_paid(oid, client_email="buyer@dogfood.test")
print("  notify result:", result.get("notifications"))
assert result.get("notifications", {}).get("webhook"), "webhook delivery failed"
PY

echo "==> Notifications dogfood OK"