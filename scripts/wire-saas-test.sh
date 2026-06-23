#!/usr/bin/env bash
# Switch Plutus SaaS to Stripe test mode (sk_test_ + test price + optional listen).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"

echo "==> Stripe test keys"
if bash "$ROOT/scripts/stripe-login-test-keys.sh"; then
  :
else
  echo ""
  echo "Manual fallback: set in $ENV_FILE"
  echo "  STRIPE_SECRET_KEY=sk_test_...   (Dashboard → Developers → API keys)"
  echo "  STRIPE_WEBHOOK_SECRET=whsec_... (from: bash scripts/stripe-listen.sh)"
  echo "Then: python3 scripts/stripe_setup.py --write-env"
  exit 1
fi

echo "==> Restart plutus-saas"
systemctl --user restart plutus-saas 2>/dev/null || true
sleep 2

echo "==> Health"
curl -sf http://127.0.0.1:8031/healthz | python3 -c "
import json,sys
b=json.load(sys.stdin)['checks']['billing']
print('  billing:', b)
assert b.get('test_mode'), 'expected test_mode after wiring sk_test_'
"

echo ""
echo "Optional — forward webhooks in another terminal:"
echo "  bash scripts/stripe-listen.sh"
echo "Copy whsec_... into STRIPE_WEBHOOK_SECRET in .env, then restart plutus-saas."