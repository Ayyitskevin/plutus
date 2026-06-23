#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export PLUTUS_SAAS_MODE="${PLUTUS_SAAS_MODE:-true}"
HOST="${PLUTUS_HOST:-0.0.0.0}"
PORT="${PLUTUS_PORT:-8031}"

if [[ -d .venv ]]; then
  source .venv/bin/activate
fi

exec uvicorn app.main:app --host "$HOST" --port "$PORT"