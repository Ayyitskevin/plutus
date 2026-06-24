#!/usr/bin/env bash
# Set PLUTUS_SAAS_PUBLIC_URL for share links, Stripe redirects, and client track pages.
# Optional: expose :8031 on the Tailscale tailnet via `tailscale serve`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
PORT="${PLUTUS_PORT:-8031}"
PUBLIC_URL="${PLUTUS_SAAS_PUBLIC_URL:-}"
TAILSCALE_SERVE="${PLUTUS_TAILSCALE_SERVE:-}"

if [[ "${1:-}" == --tailscale ]]; then
  TAILSCALE_SERVE=1
  PUBLIC_URL=""
elif [[ -z "$PUBLIC_URL" && -n "${1:-}" ]]; then
  PUBLIC_URL="$1"
fi

if [[ -z "$PUBLIC_URL" && "$TAILSCALE_SERVE" == "1" ]]; then
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "tailscale not installed — set PLUTUS_SAAS_PUBLIC_URL manually" >&2
    exit 1
  fi
  DNS_NAME=$(tailscale status --json | python3 -c "
import json, sys
self_host = json.load(sys.stdin).get('Self', {}).get('DNSName', '')
print(self_host.rstrip('.'))
")
  if [[ -z "$DNS_NAME" ]]; then
    echo "could not read tailscale DNS name" >&2
    exit 1
  fi
  echo "==> Tailscale serve https://$DNS_NAME → 127.0.0.1:${PORT}"
  if ! tailscale serve --bg --https=443 "http://127.0.0.1:${PORT}" 2>&1; then
    echo "tailscale serve failed — run once: sudo tailscale set --operator=\$USER" >&2
    echo "then: bash scripts/wire-public-url.sh --tailscale" >&2
    exit 1
  fi
  PUBLIC_URL="https://${DNS_NAME}"
fi

if [[ -z "$PUBLIC_URL" ]]; then
  echo "Usage: PLUTUS_SAAS_PUBLIC_URL=https://app.example.com bash scripts/wire-public-url.sh" >&2
  echo "   or: bash scripts/wire-public-url.sh https://app.example.com" >&2
  echo "   or: bash scripts/wire-public-url.sh --tailscale" >&2
  exit 1
fi

PUBLIC_URL="${PUBLIC_URL%/}"

echo "==> Wire public URL → $PUBLIC_URL"
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
print("wrote", env_path)
PY

echo "==> Restart plutus-saas"
systemctl --user restart plutus-saas
sleep 2

echo "==> Verify storefront base URL"
curl -sf "http://127.0.0.1:${PORT}/healthz" | python3 -c "
import json, sys
h = json.load(sys.stdin)
print('  status:', h['status'])
"

echo "Done — SaaS public base is ${PUBLIC_URL}"