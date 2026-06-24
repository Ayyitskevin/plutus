#!/usr/bin/env bash
# Tier 8 — Mise SaaS UI wiring + money-loop reference scripts (GitHub-first; no prod deploy).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Tier 8 (repo scripts — skip live deploy unless PLUTUS_TIER8_DEPLOY=1)"

if [[ "${PLUTUS_TIER8_DEPLOY:-}" == "1" ]]; then
  bash "$ROOT/scripts/wire-mise-saas.sh"
else
  echo "==> Mise SaaS wiring skipped (set PLUTUS_TIER8_DEPLOY=1 to apply .env + restart)"
fi

echo "==> Unit tests (Mise UI + client email)"
PLUTUS_DATABASE_URL= "$ROOT/.venv/bin/pytest" tests/test_mise_saas_ui.py tests/test_client_paid_email.py -q

if [[ "${PLUTUS_TIER8_DEPLOY:-}" == "1" ]] && curl -sf http://flow:8400/healthz >/dev/null 2>&1; then
  bash "$ROOT/scripts/dogfood-mise-saas.sh"
else
  echo "==> Live Mise dogfood skipped (flow offline or PLUTUS_TIER8_DEPLOY unset)"
fi

echo "==> Tier 8 complete — code on GitHub; deploy when ready with PLUTUS_TIER8_DEPLOY=1"