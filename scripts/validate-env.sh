#!/usr/bin/env bash
# Fail fast on placeholder secrets before deploy.
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

if [[ "${PLUTUS_SAAS_MODE:-false}" == "true" ]]; then
  check_not_placeholder PLUTUS_API_TOKEN
  check_not_placeholder PLUTUS_TENANT_KEY_PEPPER
  if [[ "${PLUTUS_TENANT_KEY_PEPPER:-}" == "${PLUTUS_API_TOKEN:-}" ]]; then
    echo "ERROR: PLUTUS_TENANT_KEY_PEPPER must differ from PLUTUS_API_TOKEN"
    errors=$((errors + 1))
  fi
  if [[ "${PLUTUS_TENANT_KEY_PEPPER:-}" == "plutus-dev-pepper" ]]; then
    echo "ERROR: PLUTUS_TENANT_KEY_PEPPER is the dev default"
    errors=$((errors + 1))
  fi
  if [[ -n "${STRIPE_SECRET_KEY:-}" ]]; then
    check_not_placeholder STRIPE_SECRET_KEY
  fi
  if [[ "${PLUTUS_RATE_LIMIT_ENABLED:-true}" == "true" ]]; then
    check_not_placeholder PLUTUS_REDIS_URL
  else
    echo "WARN: PLUTUS_RATE_LIMIT_ENABLED=false — per-IP/tenant limits off in SaaS"
  fi
  if [[ -n "${STRIPE_SECRET_KEY:-}" && -z "${STRIPE_WEBHOOK_SECRET:-}" ]]; then
    echo "ERROR: STRIPE_SECRET_KEY set but STRIPE_WEBHOOK_SECRET unset"
    errors=$((errors + 1))
  fi
  if [[ -n "${PLUTUS_MISE_HOOK_TOKEN:-}" ]]; then
    check_not_placeholder PLUTUS_MISE_HOOK_TENANT_ID
  fi
fi

if [[ "$errors" -gt 0 ]]; then
  echo "validate-env: $errors error(s)"
  exit 1
fi
echo "validate-env: OK ($ENV_FILE)"