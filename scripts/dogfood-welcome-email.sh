#!/usr/bin/env bash
# Dogfood admin welcome email via local SMTP catcher.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" 2>/dev/null || true
PYTHON="${PYTHON:-python3}"

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
"$PYTHON" "$ROOT/scripts/smtp-catcher.py" "$PORT" &
CATCHER_PID=$!
sleep 0.5

TENANT_ID="welcome-$(date +%s | tail -c 6)"
EMAIL="welcome@${TENANT_ID}.test"

echo "==> send_tenant_welcome_email via SMTP catcher :${PORT}"
PLUTUS_SMTP_HOST=127.0.0.1 \
PLUTUS_SMTP_PORT="$PORT" \
PLUTUS_SMTP_FROM=plutus@dogfood.test \
"$PYTHON" - <<PY
import os
import sys

sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
os.environ["PLUTUS_SMTP_HOST"] = "127.0.0.1"
os.environ["PLUTUS_SMTP_PORT"] = "${PORT}"
os.environ["PLUTUS_SMTP_FROM"] = "plutus@dogfood.test"
os.environ.pop("PLUTUS_SMTP_USER", None)
os.environ.pop("PLUTUS_SMTP_PASSWORD", None)
os.environ["PLUTUS_SAAS_PUBLIC_URL"] = "http://plutus.test"

from app import config, db, notifications, tenants

db.migrate()
tid = "${TENANT_ID}"
tenants.create_tenant(tid, name="Welcome Studio", store_slug="${TENANT_ID}")
tenant = db.get_tenant(tid)
from app import tenant_invite

token = tenant_invite.create_invite(tenant_id=tid, email="${EMAIL}")
assert notifications.send_tenant_welcome_email(
    to="${EMAIL}",
    tenant=tenant,
    invite_url=tenant_invite.claim_url(token),
), "welcome email failed"
print("  sent to ${EMAIL}")
PY

"$PYTHON" - <<'PY'
import json
import os

log = os.environ["PLUTUS_SMTP_CATCHER_LOG"]
entries = []
with open(log, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            entries.append(json.loads(line))
assert entries, "no SMTP messages captured"
body = entries[0].get("body") or ""
assert "plutus.test/ui/saas/claim-invite" in body, body[:300]
assert "plutus_tk_" not in body, body[:300]
print("  welcome body OK — invite link present, no raw API key")
PY

echo "==> Welcome email dogfood OK"