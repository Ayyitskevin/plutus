#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  cp .env.saas.example .env
  echo "Created .env from .env.saas.example — edit secrets before production."
fi

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]" -q
python -c "from app import db; db.migrate(); print('schema ok')"

ADMIN_TOKEN="${PLUTUS_API_TOKEN:-}"
if [[ -z "$ADMIN_TOKEN" || "$ADMIN_TOKEN" == *CHANGE_ME* ]]; then
  echo "Set PLUTUS_API_TOKEN in .env, then re-run to create bootstrap tenant."
  exit 0
fi

export PLUTUS_SAAS_MODE=true
python - <<'PY'
from app import config, db, tenants

db.migrate()
if not db.get_tenant("demo"):
    tenants.create_tenant("demo", name="Demo Studio", store_slug="demo-studio", monthly_recommend_cap=50)
    issued = tenants.issue_api_key("demo", label="bootstrap")
    print("Bootstrap tenant: demo")
    print("Store: /store/demo-studio")
    print("API key (save now):", issued["api_key"])
else:
    print("Bootstrap tenant demo already exists")
PY

echo "Start with:  plutus-api  (or uvicorn app.main:app --host 0.0.0.0 --port 8031)"