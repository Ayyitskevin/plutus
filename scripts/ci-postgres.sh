#!/usr/bin/env bash
# Postgres test pass — requires PLUTUS_TEST_DATABASE_URL (service URL in CI).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${PLUTUS_TEST_DATABASE_URL:-}" ]]; then
  echo "Skip Postgres CI — set PLUTUS_TEST_DATABASE_URL"
  exit 0
fi

if [[ -x "$ROOT/.venv/bin/ruff" ]]; then
  PY="$ROOT/.venv/bin"
elif command -v ruff >/dev/null 2>&1; then
  PY=""
else
  pip install -q -e ".[dev]"
  PY="$ROOT/.venv/bin"
fi

RUFF="${PY:+$PY/}ruff"
PYTEST="${PY:+$PY/}pytest"

echo "==> ruff check"
"$RUFF" check app tests

echo "==> pytest (SQLite suite + Postgres backend tests)"
PLUTUS_DATABASE_URL= PLUTUS_TEST_DATABASE_URL="$PLUTUS_TEST_DATABASE_URL" \
  "$PYTEST" tests/ -q

echo "==> Postgres CI OK"