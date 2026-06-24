#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DEST="$UNIT_DIR/plutus-saas.service"

echo "==> Plutus SaaS user service from $ROOT"

if [[ ! -f "$ROOT/.venv/bin/uvicorn" ]]; then
  echo "Missing venv — run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev,saas]'"
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  if [[ -f "$ROOT/.env.saas.example" ]]; then
    cp "$ROOT/.env.saas.example" "$ROOT/.env"
  else
    cp "$ROOT/.env.example" "$ROOT/.env"
  fi
  echo "==> Created $ROOT/.env — edit secrets before production"
fi

mkdir -p "$UNIT_DIR"
sed "s|%h|$HOME|g" "$ROOT/ops/plutus-saas-user.service" > "$DEST"

PORT="${PLUTUS_PORT:-8031}"
pkill -f "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}" 2>/dev/null || true
sleep 1

systemctl --user daemon-reload
systemctl --user enable --now plutus-saas.service

if loginctl show-user "$(whoami)" -p Linger 2>/dev/null | grep -q 'Linger=no'; then
  echo ""
  echo "NOTE: Linger is off — Plutus SaaS stops when you log out."
  echo "  Enable once (needs sudo): sudo loginctl enable-linger $(whoami)"
fi

sleep 1
systemctl --user is-active plutus-saas.service
curl -sf "http://127.0.0.1:${PORT}/healthz" | head -c 300
echo
echo "==> Plutus SaaS running on :${PORT}. Logs: journalctl --user -u plutus-saas -f"