#!/usr/bin/env bash
# Template Cloudflare tunnel for Plutus SaaS + set PLUTUS_SAAS_PUBLIC_URL.
# Does not create the tunnel (requires cloudflared login on your machine).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
HOSTNAME="${PLUTUS_PUBLIC_HOSTNAME:-plutus.kleephotography.com}"
PUBLIC_URL="https://${HOSTNAME}"
CF_DIR="${HOME}/.cloudflared"
CF_CONFIG="${CF_DIR}/plutus.yml"
PORT="${PLUTUS_PORT:-8031}"

mkdir -p "$CF_DIR"
if [[ ! -f "$CF_CONFIG" ]]; then
  cp "$ROOT/ops/cloudflare-tunnel.example.yml" "$CF_CONFIG"
  echo "Wrote template $CF_CONFIG — replace tunnel UUID and credentials-file"
fi

python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {"PLUTUS_SAAS_PUBLIC_URL": "${PUBLIC_URL}"}
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
print("wrote PLUTUS_SAAS_PUBLIC_URL to", env_path)
PY

mkdir -p "$HOME/.config/systemd/user"
cp "$ROOT/ops/plutus-cloudflared-user.service" "$HOME/.config/systemd/user/plutus-cloudflared.service"
systemctl --user daemon-reload

echo ""
echo "Next steps (one-time, on a machine with cloudflared):"
echo "  cloudflared tunnel create plutus-saas"
echo "  cloudflared tunnel route dns plutus-saas ${HOSTNAME}"
echo "  edit ${CF_CONFIG} with tunnel UUID + credentials path"
echo "  systemctl --user enable --now plutus-cloudflared"
echo "  systemctl --user restart plutus-saas"
echo ""
echo "Public URL will be ${PUBLIC_URL} (local service :${PORT})"