#!/usr/bin/env bash
# Forward Stripe webhooks to local Plutus SaaS (requires Stripe CLI).
set -euo pipefail
PORT="${PLUTUS_PORT:-8031}"
HOST="${PLUTUS_HOST:-127.0.0.1}"
FORWARD="http://${HOST}:${PORT}/webhooks/stripe"

if ! command -v stripe >/dev/null 2>&1; then
  echo "Stripe CLI not installed." >&2
  echo "  https://docs.stripe.com/stripe-cli" >&2
  echo "Or set STRIPE_WEBHOOK_SECRET manually and use dogfood-stripe-real.sh" >&2
  exit 1
fi

echo "Forwarding Stripe events → ${FORWARD}"
echo "Copy the whsec_... secret into .env as STRIPE_WEBHOOK_SECRET"
exec stripe listen --forward-to "${FORWARD}"