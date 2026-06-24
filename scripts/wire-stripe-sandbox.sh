#!/usr/bin/env bash
# Keep Plutus reachable but block real Stripe charges (live keys stay in .env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"

python3 - <<PY
from pathlib import Path

path = Path("${ENV_FILE}")
lines = path.read_text().splitlines() if path.exists() else []
out, found_live, found_sim = [], False, False
for line in lines:
    if line.startswith("PLUTUS_STRIPE_LIVE_ENABLED="):
        out.append("PLUTUS_STRIPE_LIVE_ENABLED=false")
        found_live = True
        continue
    if line.startswith("PLUTUS_ALLOW_SIMULATE_PAYMENT="):
        out.append("PLUTUS_ALLOW_SIMULATE_PAYMENT=false")
        found_sim = True
        continue
    out.append(line)
if not found_live:
    out.append("PLUTUS_STRIPE_LIVE_ENABLED=false")
if not found_sim:
    out.append("PLUTUS_ALLOW_SIMULATE_PAYMENT=false")
path.write_text("\n".join(out).rstrip() + "\n")
print(f"wrote sandbox flags to {path}")
PY

echo "==> Restart plutus-saas (if installed)"
systemctl --user restart plutus-saas 2>/dev/null || true
sleep 2

if curl -sf http://127.0.0.1:8031/saas/status >/dev/null 2>&1; then
  curl -sf http://127.0.0.1:8031/saas/status | python3 -c "
import json, sys
b = json.load(sys.stdin)['billing']
print('  billing:', b)
assert not b.get('payments_allowed') or b.get('test_mode'), b
print('  sandbox OK — no live charges')
"
else
  echo "  (service not on :8031 here — restart on the host running prod)"
fi

echo ""
echo "To accept real payments later: PLUTUS_STRIPE_LIVE_ENABLED=true in .env, then restart."
echo "For dogfood without cards: bash scripts/wire-saas-test.sh (sk_test_ keys)."