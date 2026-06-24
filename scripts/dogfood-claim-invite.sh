#!/usr/bin/env bash
# Dogfood invite claim E2E — welcome email with claim link, then UI reveals API key.
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

TENANT_ID="claim-$(date +%s | tail -c 6)"
EMAIL="claim@${TENANT_ID}.test"

echo "==> Mint invite + welcome email (SMTP catcher :${PORT})"
PLUTUS_SMTP_HOST=127.0.0.1 \
PLUTUS_SMTP_PORT="$PORT" \
PLUTUS_SMTP_FROM=plutus@dogfood.test \
"$PYTHON" - <<PY
import os
import re
import sys

sys.path.insert(0, "${ROOT}")
os.chdir("${ROOT}")
os.environ["PLUTUS_SMTP_HOST"] = "127.0.0.1"
os.environ["PLUTUS_SMTP_PORT"] = "${PORT}"
os.environ["PLUTUS_SMTP_FROM"] = "plutus@dogfood.test"
os.environ["PLUTUS_SMTP_USER"] = ""
os.environ["PLUTUS_SMTP_PASSWORD"] = ""
os.environ.pop("PLUTUS_DATABASE_URL", None)
os.environ["PLUTUS_SAAS_PUBLIC_URL"] = "http://plutus.test"
os.environ["PLUTUS_SAAS_MODE"] = "true"
os.environ["PLUTUS_API_TOKEN"] = "dogfood-admin"
os.environ["PLUTUS_TENANT_KEY_PEPPER"] = "dogfood-pepper"
os.environ["PLUTUS_RATE_LIMIT_ENABLED"] = "false"

from fastapi.testclient import TestClient

from app import config, db, notifications, tenant_invite, tenants

db.migrate()
tid = "${TENANT_ID}"
tenants.create_tenant(tid, name="Claim Studio", store_slug="${TENANT_ID}")
tenant = db.get_tenant(tid)
token = tenant_invite.create_invite(tenant_id=tid, email="${EMAIL}")
claim = tenant_invite.claim_url(token)
assert notifications.send_tenant_welcome_email(
    to="${EMAIL}",
    tenant=tenant,
    invite_url=claim,
), "welcome email failed"

log_path = os.environ["PLUTUS_SMTP_CATCHER_LOG"]
import json

body = ""
with open(log_path, encoding="utf-8") as fh:
    for line in fh:
        if line.strip():
            body = json.loads(line).get("body") or ""
assert claim in body, body[:400]

from app.main import app

client = TestClient(app)
before = len(db.list_tenant_keys(tid))
r = client.get(f"/ui/saas/claim-invite?token={token}")
assert r.status_code == 200, r.text[:400]
assert b"plutus_tk_" in r.content
assert "plutus_sid" in r.cookies
after = db.list_tenant_keys(tid)
assert len(after) == before + 1
assert after[0]["label"] == "invite"
r2 = client.get(f"/ui/saas/claim-invite?token={token}")
assert b"already claimed" in r2.content
print("  claim OK — API key issued + session cookie")
PY

echo "==> Invite claim dogfood OK"