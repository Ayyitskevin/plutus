#!/usr/bin/env bash
# Tier 7 production wiring — Dionysus SaaS, public URL, optional R2/WHCC when creds exist.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Tier 7 wiring"

echo "==> Dionysus → SaaS :8031"
bash "$ROOT/scripts/wire-dionysus-saas.sh"

if [[ -n "${PLUTUS_SAAS_PUBLIC_URL:-}" ]]; then
  echo "==> Public URL (from env)"
  bash "$ROOT/scripts/wire-public-url.sh"
elif [[ "${PLUTUS_TAILSCALE_SERVE:-}" == "1" ]]; then
  echo "==> Public URL (tailscale serve)"
  bash "$ROOT/scripts/wire-public-url.sh" --tailscale
else
  echo "==> Public URL skipped (set PLUTUS_SAAS_PUBLIC_URL or PLUTUS_TAILSCALE_SERVE=1)"
fi

if [[ -n "${R2_ACCOUNT_ID:-}" && -n "${R2_ACCESS_KEY_ID:-}" && -n "${R2_SECRET_ACCESS_KEY:-}" ]]; then
  echo "==> Cloudflare R2"
  bash "$ROOT/scripts/wire-r2.sh"
else
  echo "==> R2 skipped (set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)"
fi

if [[ -n "${WHCC_API_KEY:-}" ]]; then
  echo "==> WHCC lab (homelab)"
  PLUTUS_ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}" bash "$ROOT/scripts/wire-whcc.sh"
else
  echo "==> WHCC skipped (set WHCC_API_KEY)"
fi

echo "==> Dogfood"
bash "$ROOT/scripts/dogfood-dionysus-saas.sh"

bash "$ROOT/scripts/dogfood-public-url.sh"

echo "==> Tier 7 wiring complete"