#!/usr/bin/env bash
# Advance mock lab fulfillment through shipped → verify client track page.
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
ORDER_ID="${PLUTUS_DOGFOOD_ORDER_ID:-}"

echo "==> Health (lab adapter)"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
lab=json.load(sys.stdin)['checks']['lab']
print('  lab:', lab)
assert lab.get('enabled'), lab
"

echo "==> Advance lab status (instant mock timings)"
ADVANCE=$(cd "$ROOT" && source .venv/bin/activate && PLUTUS_DATABASE_URL= \
  ROOT="$ROOT" PLUTUS_ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["ROOT"])
os.chdir(os.environ["ROOT"])
from dotenv import load_dotenv

load_dotenv(os.environ.get("PLUTUS_ENV_FILE", ".env.homelab"), override=True)
from app import config, db, lab

if os.environ.get("PLUTUS_DATABASE_URL"):
    config.DATABASE_URL = os.environ["PLUTUS_DATABASE_URL"]

config.LAB_MOCK_PROCESS_SECONDS = 0
config.LAB_MOCK_SHIP_SECONDS = 0
db.migrate()

order_id = os.environ.get("PLUTUS_DOGFOOD_ORDER_ID", "").strip()
if order_id:
    oid = int(order_id)
else:
    rows = db.list_orders_pending_lab_poll(limit=20)
    paid = [r for r in rows if r.get("status") == "paid"]
    if not paid:
        row = db.one(
            "SELECT id FROM orders WHERE status='paid' ORDER BY id DESC LIMIT 1"
        ) if hasattr(db, "one") else None
        if not row:
            import sqlite3
            con = sqlite3.connect(config.DB_PATH)
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT id FROM orders WHERE status='paid' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            raise SystemExit("no paid orders found")
        oid = int(row["id"])
    else:
        oid = int(paid[0]["id"])

order = db.get_order(oid)
if not order:
    raise SystemExit(f"order {oid} not found")
if order.get("lab_status") in {None, ""}:
    lab.submit_order(oid)

steps = 0
while steps < 6:
    result = lab.poll_order(oid)
    steps += 1
    status = result.get("lab_status")
    if status in {"complete", "skipped", "canceled"}:
        break
    if not result.get("advanced"):
        break

order = db.get_order(oid)
print(order["id"])
print(order.get("lab_status"))
print(order.get("client_token") or "")
PY
)

ORDER_ID=$(echo "$ADVANCE" | sed -n '1p')
LAB_STATUS=$(echo "$ADVANCE" | sed -n '2p')
TRACK_TOKEN=$(echo "$ADVANCE" | sed -n '3p')
echo "  order_id=$ORDER_ID lab_status=$LAB_STATUS"

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

echo "==> Lab fulfillment dogfood OK — order #${ORDER_ID} · ${LAB_STATUS}"