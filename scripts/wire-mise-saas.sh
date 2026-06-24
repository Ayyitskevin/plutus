#!/usr/bin/env bash
# Wire Plutus SaaS to flow Mise (:8400) for published-gallery recommends.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
MISE_URL="${PLUTUS_MISE_URL:-http://flow:8400}"
MISE_TOKEN="${PLUTUS_MISE_API_TOKEN:-6e885f7b784c62b27e08be293da85108d13c1afbc0a02bfaa0bc97d9786fb57d}"
MEDIA_ROOT="${PLUTUS_MISE_MEDIA_ROOT:-$HOME/ai-workspace/argus/data/mise-media}"

echo "==> Wire Mise settings → $ENV_FILE"
python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {
    "PLUTUS_MISE_URL": "${MISE_URL}",
    "PLUTUS_MISE_API_TOKEN": "${MISE_TOKEN}",
    "PLUTUS_MISE_MEDIA_ROOT": "${MEDIA_ROOT}",
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
print("wrote Mise settings to", env_path)
for key in updates:
    if "TOKEN" in key:
        print(f"  {key}=***")
    else:
        print(f"  {key}={updates[key]}")
PY

echo "==> Optional: sync published originals from flow"
if [[ -x "$ROOT/scripts/sync-mise-media.sh" ]]; then
  bash "$ROOT/scripts/sync-mise-media.sh" || echo "  (sync skipped — flow may be offline)"
fi

if systemctl --user is-active plutus-saas >/dev/null 2>&1; then
  echo "==> Restart plutus-saas"
  systemctl --user restart plutus-saas
  sleep 2
fi

curl -sf http://127.0.0.1:8031/healthz | python3 -c "
import json, sys
mise = json.load(sys.stdin)['checks']['mise']
print('  mise:', mise)
assert mise.get('configured'), mise
"

echo "Done — Mise armed for SaaS (${MISE_URL})"