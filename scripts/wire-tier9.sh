#!/usr/bin/env bash
# Tier 9 — signup verify, notify UI, Mise hook, Cloudflare tunnel templates (GitHub-first).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Tier 9 (repo — set PLUTUS_TIER9_DEPLOY=1 to apply live wiring)"

echo "==> Unit tests"
PLUTUS_DATABASE_URL= "$ROOT/.venv/bin/pytest" \
  tests/test_signup_verify.py \
  tests/test_mise_hook.py \
  tests/test_tenant_settings.py -q

if [[ "${PLUTUS_TIER9_DEPLOY:-}" == "1" ]]; then
  [[ -n "${PLUTUS_MISE_HOOK_TENANT_ID:-}" ]] && bash "$ROOT/scripts/wire-mise-hook-saas.sh"
  bash "$ROOT/scripts/wire-cloudflare-tunnel.sh" || true
  systemctl --user restart plutus-saas 2>/dev/null || true
else
  echo "==> Live wiring skipped (PLUTUS_TIER9_DEPLOY unset)"
  echo "    Deploy: PLUTUS_TIER9_DEPLOY=1 PLUTUS_MISE_HOOK_TENANT_ID=<slug> bash scripts/wire-tier9.sh"
fi

echo "==> Tier 9 complete"