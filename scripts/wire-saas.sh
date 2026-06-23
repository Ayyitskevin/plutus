#!/usr/bin/env bash
# Wire Plutus SaaS :8031 — Postgres + Stripe from homelab secrets.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .venv/bin/activate 2>/dev/null || true

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
MISE_ENV="${MISE_STRIPE_ENV:-$HOME/ai-workspace/mise-flow-sync/.env}"
PG_URL="${PLUTUS_DATABASE_URL:-postgresql://postgres:postgres@127.0.0.1:5433/plutus}"

python3 - <<PY
from pathlib import Path
import re

env_path = Path("${ENV_FILE}")
mise_path = Path("${MISE_ENV}")
pg_url = "${PG_URL}"

def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out

def upsert(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    keys = set(updates)
    out: list[str] = []
    seen: set[str] = set()
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
    path.write_text("\\n".join(out).rstrip() + "\\n")

mise = parse_env(mise_path)
updates: dict[str, str] = {
    "PLUTUS_DATABASE_URL": pg_url,
    "PLUTUS_SAAS_MODE": "true",
    "PLUTUS_PORT": "8031",
    "PLUTUS_SAAS_PUBLIC_URL": "http://127.0.0.1:8031",
}
sk = mise.get("MISE_STRIPE_SECRET_KEY") or mise.get("STRIPE_SECRET_KEY", "")
wh = mise.get("MISE_STRIPE_WEBHOOK_SECRET") or mise.get("STRIPE_WEBHOOK_SECRET", "")
if sk:
    updates["STRIPE_SECRET_KEY"] = sk
if wh:
    updates["STRIPE_WEBHOOK_SECRET"] = wh
# Homelab dogfood: allow simulate when only test keys; live keys use real checkout
if sk.startswith("sk_test_"):
    updates.setdefault("PLUTUS_ALLOW_SIMULATE_PAYMENT", "true")

upsert(env_path, updates)
print("updated", env_path)
print("postgres", pg_url)
print("stripe_key", "set" if sk else "missing")
print("stripe_mode", "test" if sk.startswith("sk_test_") else "live" if sk.startswith("sk_live_") else "unknown")
print("webhook", "set" if wh else "missing")
PY

echo "==> Ensure Postgres is running"
if ! docker ps --format '{{.Names}}' | grep -q '^plutus-pg-test$'; then
  docker run -d --name plutus-pg-test \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=plutus \
    -p 5433:5432 \
    postgres:16-alpine
  sleep 4
fi

echo "==> Migrate Postgres schema"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
python3 -c "from app import db; db.migrate(); print('backend', db.backend_name())"

echo "==> Stripe product + price (if key configured)"
if grep -q '^STRIPE_SECRET_KEY=sk_' "$ENV_FILE" 2>/dev/null && ! grep -q 'dogfood_local' "$ENV_FILE"; then
  python3 scripts/stripe_setup.py --write-env || true
fi

echo "==> Restart plutus-saas"
systemctl --user restart plutus-saas
sleep 2
curl -sf "http://127.0.0.1:8031/healthz" | python3 -c "
import json,sys
h=json.load(sys.stdin)
db=h['checks']['database']
bill=h['checks'].get('billing',{})
print('status', h['status'])
print('database', db)
print('billing', bill)
"