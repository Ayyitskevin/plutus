#!/usr/bin/env bash
# Push STRIPE_SECRET_KEY + webhook from plutus .env → flow /opt/mise/.env
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
FLOW_HOST="${MISE_FLOW_HOST:-flow}"
MISE_ENV="/opt/mise/.env"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

SK="${STRIPE_SECRET_KEY:-}"
WH="${STRIPE_WEBHOOK_SECRET:-}"
if [[ -z "$SK" ]]; then
  echo "STRIPE_SECRET_KEY missing in $ENV_FILE" >&2
  exit 1
fi

echo "==> Update $FLOW_HOST:$MISE_ENV"
ssh -o ConnectTimeout=10 "$FLOW_HOST" \
  MISE_STRIPE_SECRET_KEY="$SK" \
  MISE_STRIPE_WEBHOOK_SECRET="$WH" \
  MISE_ENV_PATH="$MISE_ENV" \
  python3 - <<'PY'
import os
from pathlib import Path

env = Path(os.environ["MISE_ENV_PATH"])
updates = {"MISE_STRIPE_SECRET_KEY": os.environ["MISE_STRIPE_SECRET_KEY"]}
wh = os.environ.get("MISE_STRIPE_WEBHOOK_SECRET", "")
if wh:
    updates["MISE_STRIPE_WEBHOOK_SECRET"] = wh
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
print("updated", sorted(updates.keys()))
PY

echo "==> Restart mise on flow (if systemd unit exists)"
ssh "$FLOW_HOST" "sudo systemctl restart mise 2>/dev/null || systemctl --user restart mise 2>/dev/null || echo '(restart mise manually on flow)'"
echo "Done — flow Mise Stripe keys synced from plutus .env"