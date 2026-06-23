#!/usr/bin/env bash
# Plutus homelab user systemd unit (.env.homelab, :8030)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DEST="$UNIT_DIR/plutus-homelab.service"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}"

echo "==> Plutus homelab user service from $ROOT"

if [[ ! -f "$ROOT/.venv/bin/uvicorn" ]]; then
  echo "Missing venv — run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ROOT/.env.homelab.example" ]]; then
    cp "$ROOT/.env.homelab.example" "$ENV_FILE"
    echo "==> Created $ENV_FILE — edit before production"
  else
    echo "Missing $ENV_FILE" >&2
    exit 1
  fi
fi

mkdir -p "$UNIT_DIR"
sed "s|%h|$HOME|g" "$ROOT/ops/plutus-homelab-user.service" > "$DEST"

PORT="${PLUTUS_PORT:-8030}"
pkill -f "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}" 2>/dev/null || true
sleep 1

systemctl --user daemon-reload
systemctl --user enable --now plutus-homelab.service

if loginctl show-user "$(whoami)" -p Linger 2>/dev/null | grep -q 'Linger=no'; then
  echo ""
  echo "NOTE: Linger is off — Plutus homelab stops when you log out."
  echo "  Enable once (needs sudo): sudo loginctl enable-linger $(whoami)"
fi

sleep 1
systemctl --user is-active plutus-homelab.service
curl -sf "http://127.0.0.1:${PORT}/healthz" | head -c 300
echo
echo "==> Plutus homelab on :${PORT}. Logs: journalctl --user -u plutus-homelab -f"