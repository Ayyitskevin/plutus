#!/usr/bin/env bash
# Hardening verification — env checks + security-focused pytest slice.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"

echo "==> validate-env"
bash "$ROOT/scripts/validate-env.sh" "$ENV_FILE"

PY="${ROOT}/.venv/bin/pytest"
if [[ ! -x "$PY" ]]; then
  pip install -q -e ".[dev]"
  PY="${ROOT}/.venv/bin/pytest"
fi

echo "==> hardening tests"
PLUTUS_DATABASE_URL= "$PY" \
  tests/test_no_money_surface.py \
  tests/test_service_tokens.py \
  tests/test_mise_hook.py \
  -q

echo "==> hardening smoke OK"