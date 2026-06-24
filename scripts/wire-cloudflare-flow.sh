#!/usr/bin/env bash
# Route plutus.kleephotography.com via flow's existing mise Cloudflare tunnel.
# Plutus SaaS runs on strix :8031; flow cloudflared proxies to strix-halo-a9-mega:8031.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
FLOW_HOST="${MISE_FLOW_HOST:-flow}"
HOSTNAME="${PLUTUS_PUBLIC_HOSTNAME:-plutus.kleephotography.com}"
PUBLIC_URL="https://${HOSTNAME}"
PLUTUS_UPSTREAM="${PLUTUS_CF_UPSTREAM:-http://strix-halo-a9-mega:8031}"
TUNNEL_NAME="${PLUTUS_CF_TUNNEL_NAME:-mise}"
CF_CONFIG_USER="/home/kevin-lee/.cloudflared/config.yml"
CF_CONFIG_SYSTEM="/etc/cloudflared/config.yml"

echo "==> Add ${HOSTNAME} → ${PLUTUS_UPSTREAM} on ${FLOW_HOST}"
ssh -o ConnectTimeout=15 "$FLOW_HOST" \
  HOSTNAME="$HOSTNAME" \
  PLUTUS_UPSTREAM="$PLUTUS_UPSTREAM" \
  CF_CONFIG="$CF_CONFIG_USER" \
  TUNNEL_NAME="$TUNNEL_NAME" \
  python3 - <<'PY'
import os
import subprocess
from pathlib import Path

cfg = Path(os.environ["CF_CONFIG"])
hostname = os.environ["HOSTNAME"]
upstream = os.environ["PLUTUS_UPSTREAM"]
lines = cfg.read_text().splitlines() if cfg.exists() else []

if any(hostname in ln for ln in lines):
    print(f"  ingress for {hostname} already present")
else:
    out, inserted = [], False
    for line in lines:
        out.append(line)
        if not inserted and line.strip() == "- service: http_status:404":
            out.insert(-1, f"  - hostname: {hostname}")
            out.insert(-1, f"    service: {upstream}")
            inserted = True
    if not inserted:
        raise SystemExit("could not find catch-all ingress rule in cloudflared config")
    cfg.write_text("\n".join(out).rstrip() + "\n")
    print(f"  wrote ingress {hostname} → {upstream}")

subprocess.run(
    ["cloudflared", "tunnel", "route", "dns", os.environ["TUNNEL_NAME"], hostname],
    check=False,
)
PY

echo "==> Activate config on flow (system cloudflared reads ${CF_CONFIG_SYSTEM})"
if ssh "$FLOW_HOST" "sudo -n cp ${CF_CONFIG_USER} ${CF_CONFIG_SYSTEM} && sudo -n systemctl restart cloudflared" 2>/dev/null; then
  ssh "$FLOW_HOST" "sleep 2 && systemctl is-active cloudflared"
else
  echo ""
  echo "ACTION REQUIRED — run once on flow (sudo password):"
  echo "  ssh flow 'sudo cp ~/.cloudflared/config.yml /etc/cloudflared/config.yml && sudo systemctl restart cloudflared'"
  echo ""
fi

echo "==> Set PLUTUS_SAAS_PUBLIC_URL=${PUBLIC_URL}"
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
env_path.write_text("\n".join(out).rstrip() + "\n")
print("  wrote", env_path)
PY

if systemctl --user is-active plutus-saas >/dev/null 2>&1; then
  echo "==> Restart plutus-saas"
  systemctl --user restart plutus-saas
  sleep 2
fi

echo "==> Probe public URL"
for i in 1 2 3 4 5 6; do
  if curl -sf --connect-timeout 8 "${PUBLIC_URL}/healthz" >/dev/null 2>&1; then
    curl -sf "${PUBLIC_URL}/healthz" | python3 -c "import json,sys; print('  public health:', json.load(sys.stdin)['status'])"
    echo "Done — Plutus SaaS public URL is ${PUBLIC_URL}"
    exit 0
  fi
  echo "  waiting for DNS/tunnel (${i}/6)..."
  sleep 5
done
echo "WARN: ${PUBLIC_URL}/healthz not reachable yet — run the sudo step on flow if you have not" >&2
exit 1