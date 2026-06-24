#!/usr/bin/env bash
# Dogfood order-paid SMTP notifications (local catcher on :2525).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .venv/bin/activate 2>/dev/null || true

PORT="${PLUTUS_SMTP_PORT_DOGFOOD:-2525}"
LOG="$(mktemp)"
CATCHER_PID=""

cleanup() {
  rm -f "$LOG"
  if [[ -n "$CATCHER_PID" ]] && kill -0 "$CATCHER_PID" 2>/dev/null; then
    kill "$CATCHER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

export PLUTUS_SMTP_CATCHER_LOG="$LOG"
python3 "$ROOT/scripts/smtp-catcher.py" "$PORT" &
CATCHER_PID=$!
sleep 0.5

echo "==> Fire notify_order_paid with PLUTUS_SMTP_HOST=127.0.0.1:$PORT"
PLUTUS_SMTP_HOST=127.0.0.1 \
PLUTUS_SMTP_PORT="$PORT" \
PLUTUS_SMTP_FROM=plutus@dogfood.test \
PLUTUS_NOTIFY_CLIENT_ON_PAID=true \
python3 - <<'PY'
import json
import os
from pathlib import Path

os.environ.setdefault("PLUTUS_DATABASE_URL", "")
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PLUTUS_SMTP_HOST"] = "127.0.0.1"
os.environ["PLUTUS_SMTP_PORT"] = os.environ.get("PLUTUS_SMTP_PORT", "2525")
os.environ["PLUTUS_SMTP_FROM"] = "plutus@dogfood.test"
os.environ.pop("PLUTUS_SMTP_USER", None)
os.environ.pop("PLUTUS_SMTP_PASSWORD", None)
os.environ["PLUTUS_NOTIFY_CLIENT_ON_PAID"] = "true"

from app import config, db, notifications, tenants

db.migrate()
tid = "smtp-dogfood"
if not db.get_tenant(tid):
    tenants.create_tenant(tid, name="SMTP Dogfood", store_slug="smtp-dogfood")
db.update_tenant(tid, notify_email="studio@dogfood.test")
gid = db.insert_gallery(name="G", source="/x", photo_count=1, tenant_id=tid)
rid = db.insert_run(
    gallery_id=gid,
    engine="mock",
    bundle_count=1,
    estimated_total_cents=4500,
    payload={
        "bundles": [
            {
                "title": "Gallery Favorites",
                "items": [
                    {
                        "sku": "print-8x10",
                        "label": "8×10 Print",
                        "unit_cents": 4500,
                        "quantity": 1,
                        "photo": "01.jpg",
                    }
                ],
            }
        ]
    },
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
assert result.get("notifications", {}).get("email"), "photographer email failed"
assert result.get("notifications", {}).get("client_email"), "client email failed"
PY

echo "==> Verify catcher received photographer + client messages"
python3 - <<'PY'
import json
import os
import sys

log = os.environ["PLUTUS_SMTP_CATCHER_LOG"]
entries = []
with open(log, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            entries.append(json.loads(line))
recipients = {addr.strip("<>") for e in entries for addr in e.get("to") or []}
assert "studio@dogfood.test" in recipients, entries
assert "buyer@dogfood.test" in recipients, entries
client = next(e for e in entries if "buyer@dogfood.test" in str(e.get("to")))
body = client.get("body") or ""
assert "Gallery Favorites" in body, body[:500]
assert "8×10 Print" in body, body
assert "$45.00" in body, body
assert "/store/order/track/" in body, body
print("  caught", len(entries), "message(s) — client body OK")
PY

echo "==> SMTP dogfood OK"