#!/usr/bin/env bash
# Optional Postgres test pass — requires PLUTUS_TEST_DATABASE_URL
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -z "${PLUTUS_TEST_DATABASE_URL:-}" ]]; then
  echo "Skip Postgres CI — set PLUTUS_TEST_DATABASE_URL"
  exit 0
fi
.venv/bin/pytest tests/test_db_postgres.py tests/test_tier10_hardening.py -q