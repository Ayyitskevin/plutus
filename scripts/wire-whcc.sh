#!/usr/bin/env bash
# Arm WHCC lab fulfillment on Plutus (homelab or SaaS .env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
WHCC_URL="${WHCC_API_URL:-https://api.whcc.com/v1}"
WHCC_KEY="${WHCC_API_KEY:?Set WHCC_API_KEY}"
WHCC_ACCOUNT="${WHCC_ACCOUNT_ID:-}"
WHCC_WEBHOOK="${WHCC_WEBHOOK_SECRET:-}"

python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {
    "PLUTUS_LAB_ADAPTER": "whcc",
    "WHCC_API_URL": "${WHCC_URL}",
    "WHCC_API_KEY": "${WHCC_KEY}",
}
if "${WHCC_ACCOUNT}":
    updates["WHCC_ACCOUNT_ID"] = "${WHCC_ACCOUNT}"
if "${WHCC_WEBHOOK}":
    updates["WHCC_WEBHOOK_SECRET"] = "${WHCC_WEBHOOK}"
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
print("wrote WHCC settings to", env_path)
for key in updates:
    if "KEY" in key or "SECRET" in key:
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
lab = json.load(sys.stdin)['checks']['lab']
print('  lab:', lab)
assert lab.get('adapter') == 'whcc', lab
"

echo "Done — WHCC adapter armed (stub submits when API unreachable)"