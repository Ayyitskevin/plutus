#!/usr/bin/env bash
# Push MISE_PLUTUS_* from plutus .env (Mise hook settings) → flow /opt/mise/.env
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
FLOW_HOST="${MISE_FLOW_HOST:-flow}"
MISE_ENV="/opt/mise/.env"
PLUTUS_URL="${PLUTUS_SAAS_HOOK_URL:-http://strix-halo-a9-mega:8031}"
USE_WEBHOOK="${MISE_PLUTUS_USE_WEBHOOK:-true}"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

TOKEN="${PLUTUS_MISE_HOOK_TOKEN:-}"
TENANT="${PLUTUS_MISE_HOOK_TENANT_ID:-}"
if [[ -z "$TOKEN" || -z "$TENANT" ]]; then
  echo "Run scripts/wire-mise-hook-saas.sh first (PLUTUS_MISE_HOOK_* in $ENV_FILE)" >&2
  exit 1
fi

echo "==> Update $FLOW_HOST:$MISE_ENV (Plutus SaaS Mise hook)"
ssh -o ConnectTimeout=10 "$FLOW_HOST" \
  MISE_PLUTUS_URL="$PLUTUS_URL" \
  MISE_PLUTUS_TOKEN="$TOKEN" \
  MISE_PLUTUS_TENANT_ID="$TENANT" \
  MISE_PLUTUS_USE_WEBHOOK="$USE_WEBHOOK" \
  MISE_PLUTUS_TIMEOUT="${MISE_PLUTUS_TIMEOUT:-60}" \
  MISE_ENV_PATH="$MISE_ENV" \
  python3 - <<'PY'
import os
from pathlib import Path

env = Path(os.environ["MISE_ENV_PATH"])
updates = {
    "MISE_PLUTUS_URL": os.environ["MISE_PLUTUS_URL"],
    "MISE_PLUTUS_TOKEN": os.environ["MISE_PLUTUS_TOKEN"],
    "MISE_PLUTUS_TENANT_ID": os.environ["MISE_PLUTUS_TENANT_ID"],
    "MISE_PLUTUS_USE_WEBHOOK": os.environ["MISE_PLUTUS_USE_WEBHOOK"],
    "MISE_PLUTUS_TIMEOUT": os.environ["MISE_PLUTUS_TIMEOUT"],
}
lines = env.read_text().splitlines()
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
env.write_text("\n".join(out).rstrip() + "\n")
for k in sorted(updates):
    if "TOKEN" in k:
        print(f"  {k}=***")
    else:
        print(f"  {k}={updates[k]}")
PY

echo "==> Restart mise on flow"
ssh "$FLOW_HOST" "sudo systemctl restart mise 2>/dev/null || systemctl --user restart mise 2>/dev/null || echo '(restart mise manually on flow)'"

echo "Done — flow Mise publish hook → Plutus SaaS :8031"