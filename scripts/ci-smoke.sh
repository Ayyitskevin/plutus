#!/usr/bin/env bash
# CI-equivalent smoke — lint + full test suite (no live services required)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/ruff" ]]; then
  PY="$ROOT/.venv/bin"
elif command -v ruff >/dev/null 2>&1; then
  PY=""
else
  echo "==> Installing dev deps"
  pip install -q -e ".[dev]"
  PY="$ROOT/.venv/bin"
fi

RUFF="${PY:+$PY/}ruff"
PYTEST="${PY:+$PY/}pytest"

echo "==> ruff check"
"$RUFF" check app tests

echo "==> pytest"
"$PYTEST" tests/ -q

echo "==> CI smoke OK ($( "$PYTEST" tests/ --collect-only -q 2>/dev/null | tail -1 ))"