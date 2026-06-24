#!/usr/bin/env bash
# Arm Mise publish hook for Plutus SaaS (:8031) + optional flow mise-flow-sync env.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
TENANT_ID="${PLUTUS_MISE_HOOK_TENANT_ID:?Set PLUTUS_MISE_HOOK_TENANT_ID — SaaS tenant for flow galleries}"
TOKEN="${PLUTUS_MISE_HOOK_TOKEN:-${MISE_FLEET_TOKEN:-6e885f7b784c62b27e08be293da85108d13c1afbc0a02bfaa0bc97d9786fb57d}}"
FLOW_ENV="${MISE_FLOW_ENV:-/opt/mise/.env}"
PLUTUS_URL="${PLUTUS_SAAS_HOOK_URL:-http://strix-halo-a9-mega:8031}"

echo "==> Wire Plutus SaaS Mise hook → $ENV_FILE"
python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {
    "PLUTUS_MISE_HOOK_TENANT_ID": "${TENANT_ID}",
    "PLUTUS_MISE_HOOK_TOKEN": "${TOKEN}",
}
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
print("wrote Mise hook settings to", env_path)
PY

if [[ -f "$ROOT/scripts/sync-flow-mise-plutus.sh" ]]; then
  echo "==> Sync flow Mise env (optional: MISE_FLOW_HOST=... to override)"
  bash "$ROOT/scripts/sync-flow-mise-plutus.sh" || echo "  (flow sync skipped — run scripts/sync-flow-mise-plutus.sh manually)"
fi

if systemctl --user is-active plutus-saas >/dev/null 2>&1; then
  echo "==> Restart plutus-saas"
  systemctl --user restart plutus-saas
  sleep 2
fi

echo "Done — Mise publish hook armed for tenant ${TENANT_ID}"