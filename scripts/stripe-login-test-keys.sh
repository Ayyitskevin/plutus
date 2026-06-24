#!/usr/bin/env bash
# Complete Stripe CLI login and wire sk_test_ + whsec into Plutus .env
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STRIPE_BIN="${STRIPE_BIN:-/tmp/stripe}"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/stripe/config.toml"

if [[ ! -x "$STRIPE_BIN" ]]; then
  curl -sL https://github.com/stripe/stripe-cli/releases/download/v1.42.15/stripe_1.42.15_linux_x86_64.tar.gz \
    | tar xz -C /tmp
  STRIPE_BIN=/tmp/stripe
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "Stripe CLI not logged in. Run:"
  echo "  $STRIPE_BIN login --interactive"
  echo "Open the browser URL and approve, then re-run this script."
  exit 1
fi

TEST_KEY=$("$STRIPE_BIN" config --get test_mode_api_key 2>/dev/null || true)
if [[ -z "$TEST_KEY" ]]; then
  echo "No test_mode_api_key in $CONFIG." >&2
  echo "CLI login succeeded but only live_mode_api_key was stored." >&2
  echo "Fix: Stripe Dashboard → toggle Test mode (top right) → Developers → API keys" >&2
  echo "  Copy sk_test_... then either:" >&2
  echo "    $STRIPE_BIN login --interactive   (paste sk_test when prompted)" >&2
  echo "  or set STRIPE_SECRET_KEY=sk_test_... in .env and run stripe_setup.py" >&2
  exit 1
fi

python3 - <<PY
from pathlib import Path
env = Path("${ENV_FILE}")
updates = {"STRIPE_SECRET_KEY": "${TEST_KEY}"}
lines = env.read_text().splitlines() if env.exists() else []
out, seen = [], set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        k = line.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
            continue
    out.append(line)
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
env.write_text("\\n".join(out).rstrip() + "\\n")
print("wrote STRIPE_SECRET_KEY (sk_test_) to", env)
PY

echo "==> Create test-mode subscription price"
cd "$ROOT" && source .venv/bin/activate
set -a && source "$ENV_FILE" && set +a
python3 scripts/stripe_setup.py --write-env

echo "==> Start webhook forwarder (background) — copy whsec to .env"
echo "Run in another terminal: bash scripts/stripe-listen.sh"
echo "Or dogfood-stripe-real.sh signs webhooks locally without stripe listen."