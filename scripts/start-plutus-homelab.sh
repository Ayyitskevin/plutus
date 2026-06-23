#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export PLUTUS_SAAS_MODE="${PLUTUS_SAAS_MODE:-false}"
HOST="${PLUTUS_HOST:-0.0.0.0}"
PORT="${PLUTUS_PORT:-8030}"

if [[ -d .venv ]]; then
  source .venv/bin/activate
fi

exec uvicorn app.main:app --host "$HOST" --port "$PORT"