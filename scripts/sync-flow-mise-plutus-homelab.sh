#!/usr/bin/env bash
# Push MISE_PLUTUS_* from plutus .env.homelab → flow /opt/mise/.env (studio :8030).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}"
FLOW_HOST="${MISE_FLOW_HOST:-flow}"
MISE_ENV="/opt/mise/.env"
PLUTUS_URL="${MISE_PLUTUS_URL:-http://strix-halo-a9-mega:8030}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

TOKEN="${PLUTUS_API_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  echo "Set PLUTUS_API_TOKEN in $ENV_FILE" >&2
  exit 1
fi

echo "==> Update $FLOW_HOST:$MISE_ENV (Plutus homelab studio)"
ssh -o ConnectTimeout=10 "$FLOW_HOST" \
  MISE_PLUTUS_URL="$PLUTUS_URL" \
  MISE_PLUTUS_TOKEN="$TOKEN" \
  MISE_PLUTUS_TIMEOUT="${MISE_PLUTUS_TIMEOUT:-120}" \
  MISE_ENV_PATH="$MISE_ENV" \
  python3 - <<'PY'
import os
from pathlib import Path

env = Path(os.environ["MISE_ENV_PATH"])
updates = {
    "MISE_PLUTUS_URL": os.environ["MISE_PLUTUS_URL"],
    "MISE_PLUTUS_TOKEN": os.environ["MISE_PLUTUS_TOKEN"],
    "MISE_PLUTUS_TIMEOUT": os.environ["MISE_PLUTUS_TIMEOUT"],
}
lines = env.read_text().splitlines() if env.exists() else []
out = []
seen = set()
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.strip().startswith("#") else None
    if key in updates:
        if key not in seen:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        continue
    out.append(line)
for key, val in updates.items():
    if key not in seen:
        out.append(f"{key}={val}")
env.write_text("\n".join(out).rstrip() + "\n")
print("updated", ", ".join(updates))
PY

ssh "$FLOW_HOST" "systemctl is-active mise && sudo systemctl restart mise" 2>/dev/null \
  || ssh "$FLOW_HOST" "systemctl is-active mise" 2>/dev/null \
  || echo "WARN: could not restart mise on flow"

echo "==> flow Mise → Plutus homelab wired ($PLUTUS_URL)"