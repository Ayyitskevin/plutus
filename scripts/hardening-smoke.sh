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
  pip install -q -e ".[dev,saas]"
  PY="${ROOT}/.venv/bin/pytest"
fi

echo "==> hardening tests"
PLUTUS_DATABASE_URL= "$PY" \
  tests/test_money_path_hardening.py \
  tests/test_tier10_hardening.py \
  tests/test_tier13_hardening.py \
  tests/test_saas_startup_hardening.py \
  tests/test_mise_hook.py \
  tests/test_lab_whcc.py \
  tests/test_upload_worker_idempotent.py \
  -q

echo "==> hardening smoke OK"