#!/usr/bin/env bash
# Wire PLUTUS_ORDER_WEBHOOK_URL into .env (Discord/Slack/custom hook).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
URL="${1:-}"

if [[ -z "$URL" ]]; then
  echo "Usage: $0 <webhook-url>" >&2
  echo "  Local dogfood catcher: http://127.0.0.1:9999/plutus-events" >&2
  echo "  Discord: https://discord.com/api/webhooks/..." >&2
  exit 1
fi

python3 - <<PY
from pathlib import Path
env = Path("${ENV_FILE}")
updates = {
    "PLUTUS_ORDER_WEBHOOK_URL": "${URL}",
    "PLUTUS_NOTIFY_LAB_SHIPPED": "true",
}
lines = env.read_text().splitlines() if env.exists() else []
out, seen = [], set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        k = line.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
            continue
    out.append(line)
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
env.write_text("\\n".join(out).rstrip() + "\\n")
print("wrote", list(updates.keys()), "to", env)
PY

systemctl --user restart plutus-saas 2>/dev/null || true
echo "Restart plutus-saas if running. Test: bash scripts/dogfood-notifications.sh"