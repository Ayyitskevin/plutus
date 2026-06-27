#!/usr/bin/env bash
# Fail fast on placeholder secrets before deploy (single-operator Mise worker).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-$ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "validate-env: missing $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

errors=0

check_not_placeholder() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" ]]; then
    echo "ERROR: $name is unset"
    errors=$((errors + 1))
    return
  fi
  if [[ "$value" == *CHANGE_ME* ]]; then
    echo "ERROR: $name still contains CHANGE_ME"
    errors=$((errors + 1))
  fi
}

# Inbound shared secret with Mise (== Mise's MISE_PLUTUS_TOKEN).
check_not_placeholder PLUTUS_API_TOKEN

# Mise read API, when wired, needs a real bearer.
if [[ -n "${PLUTUS_MISE_URL:-}" ]]; then
  check_not_placeholder PLUTUS_MISE_API_TOKEN
fi

if [[ "$errors" -gt 0 ]]; then
  echo "validate-env: $errors error(s)"
  exit 1
fi
echo "validate-env: OK ($ENV_FILE)"
