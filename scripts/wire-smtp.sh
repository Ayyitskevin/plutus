#!/usr/bin/env bash
# Arm SMTP order notifications on Plutus (homelab or SaaS .env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
SMTP_HOST="${PLUTUS_SMTP_HOST:?Set PLUTUS_SMTP_HOST}"
SMTP_FROM="${PLUTUS_SMTP_FROM:?Set PLUTUS_SMTP_FROM}"
SMTP_PORT="${PLUTUS_SMTP_PORT:-587}"
SMTP_USER="${PLUTUS_SMTP_USER:-}"
SMTP_PASSWORD="${PLUTUS_SMTP_PASSWORD:-}"

python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {
    "PLUTUS_SMTP_HOST": "${SMTP_HOST}",
    "PLUTUS_SMTP_PORT": "${SMTP_PORT}",
    "PLUTUS_SMTP_FROM": "${SMTP_FROM}",
    "PLUTUS_NOTIFY_CLIENT_ON_PAID": "true",
    "PLUTUS_NOTIFY_LAB_SHIPPED": "true",
}
if "${SMTP_USER}":
    updates["PLUTUS_SMTP_USER"] = "${SMTP_USER}"
if "${SMTP_PASSWORD}":
    updates["PLUTUS_SMTP_PASSWORD"] = "${SMTP_PASSWORD}"
lines = env_path.read_text().splitlines() if env_path.exists() else []
out, seen = [], set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_path.write_text("\\n".join(out).rstrip() + "\\n")
print("wrote SMTP settings to", env_path)
for key in updates:
    if "PASSWORD" in key:
        print(f"  {key}=***")
    else:
        print(f"  {key}={updates[key]}")
PY

SERVICE=""
if systemctl --user is-active plutus-saas >/dev/null 2>&1 && [[ "$ENV_FILE" == *".env" ]]; then
  SERVICE=plutus-saas
elif systemctl --user is-active plutus-homelab >/dev/null 2>&1; then
  SERVICE=plutus-homelab
fi
if [[ -n "$SERVICE" ]]; then
  echo "==> Restart $SERVICE"
  systemctl --user restart "$SERVICE"
  sleep 2
fi

PORT=8031
[[ "$ENV_FILE" == *homelab* ]] && PORT=8030
curl -sf "http://127.0.0.1:${PORT}/healthz" | python3 -c "
import json, sys
notes = json.load(sys.stdin)['checks']['notifications']
print('  notifications:', notes)
assert notes.get('smtp'), notes
"

echo "Done — SMTP armed. Test: bash scripts/dogfood-smtp.sh"