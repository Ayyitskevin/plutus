#!/usr/bin/env bash
# SaaS Mise gallery → recommend → run (requires flow Mise + synced media).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-session.sh"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"
MISE_URL="${PLUTUS_MISE_URL:-http://flow:8400}"
GALLERY_ID="${MISE_DOGFOOD_GALLERY_ID:-1}"

echo "==> Health (Mise on SaaS)"
curl -sf "$BASE/healthz" | python3 -c "
import json,sys
m=json.load(sys.stdin)['checks']['mise']
print('  mise:', m)
assert m.get('configured'), 'run scripts/wire-mise-saas.sh first'
"

echo "==> Find published gallery on Mise"
GALLERY=$(curl -sfS -H "Authorization: Bearer ${PLUTUS_MISE_API_TOKEN}" \
  "${MISE_URL}/api/galleries?published=true" | python3 -c "
import json, sys
rows = json.load(sys.stdin).get('galleries') or []
match = next((g for g in rows if int(g['id']) == int('${GALLERY_ID}')), None)
if not match:
    published = [g for g in rows if g.get('published')]
    if not published:
        raise SystemExit('no published Mise galleries')
    match = published[0]
if not match.get('published'):
    raise SystemExit(f'gallery {match[\"id\"]} not published')
print(json.dumps(match))
")
GID=$(echo "$GALLERY" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "  gallery_id=$GID"

PLUTUS_DOGFOOD_ROOT="$ROOT"
echo "==> Dogfood tenant + recommend via API"
SLUG="m$(date +%s | tail -c 6)"
dogfood_bootstrap_tenant "$SLUG" "Mise Studio"
API_KEY="$PLUTUS_DOGFOOD_API_KEY"
test -n "$API_KEY"

RUN_JSON=$(curl -sf -X POST "$BASE/recommend/mise-gallery" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d "mise_gallery_id=${GID}")
RUN_ID=$(echo "$RUN_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])")
BUNDLES=$(echo "$RUN_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('bundles') or []))")
echo "  run_id=$RUN_ID bundles=$BUNDLES"
test "$BUNDLES" -gt 0

PITCH=$(curl -sf -H "Authorization: Bearer ${API_KEY}" "${BASE}/runs/${RUN_ID}/pitch.txt")
echo "$PITCH" | head -12
echo "$PITCH" | grep -qi "bundle\|▸" || { echo "pitch missing bundles" >&2; exit 1; }

echo "==> Mise SaaS dogfood OK — gallery ${GID} → run ${RUN_ID}"