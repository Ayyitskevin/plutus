#!/usr/bin/env bash
# Verify flow Mise gallery → Argus → Plutus → client pitch.txt on homelab :8030.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

MISE_URL="${PLUTUS_MISE_URL:-http://flow:8400}"
PLUTUS_URL="${PLUTUS_HOMELAB_URL:-http://127.0.0.1:8030}"
TOKEN="${PLUTUS_API_TOKEN:-6e885f7b784c62b27e08be293da85108d13c1afbc0a02bfaa0bc97d9786fb57d}"
GALLERY_ID="${MISE_DOGFOOD_GALLERY_ID:-1}"

echo "==> Mise gallery #${GALLERY_ID} hook status"
GALLERY=$(curl -sfS -H "Authorization: Bearer ${TOKEN}" \
  "${MISE_URL}/api/galleries?published=false" | python3 -c "
import json, sys
rows = json.load(sys.stdin).get('galleries') or []
match = next((g for g in rows if int(g['id']) == int('${GALLERY_ID}')), None)
if not match:
    raise SystemExit('gallery ${GALLERY_ID} not found')
print(json.dumps(match))
")
echo "$GALLERY" | python3 -m json.tool

RUN_ID=$(echo "$GALLERY" | python3 -c "
import json, sys
g = json.load(sys.stdin)
status = g.get('plutus_last_status')
run_id = g.get('plutus_last_run_id')
argus = g.get('argus_last_status')
if argus != 'done':
    raise SystemExit(f'Argus not done: {argus!r}')
if status != 'done' or not run_id:
    raise SystemExit(f'Plutus not done: status={status!r} run={run_id!r}')
print(run_id)
")

echo "==> Plutus run #${RUN_ID} pitch.txt"
PITCH=$(curl -sf -H "Authorization: Bearer ${TOKEN}" "${PLUTUS_URL}/runs/${RUN_ID}/pitch.txt")
echo "$PITCH" | head -25
if ! echo "$PITCH" | grep -qi "bundle\|▸"; then
  echo "pitch missing bundle section" >&2
  exit 1
fi
if curl -sf http://127.0.0.1:8030/healthz | python3 -c "
import json,sys
d=json.load(sys.stdin)['checks'].get('dionysus') or {}
import sys as s
s.exit(0 if d.get('configured') else 1)
" 2>/dev/null; then
  if ! echo "$PITCH" | grep -qi "Keywords that sell the story"; then
    echo "WARN: Dionysus armed but pitch lacks keyword enrichment (re-run recommend for fresh keywords)" >&2
  fi
fi

echo "==> Flow gallery dogfood OK — gallery ${GALLERY_ID} → run ${RUN_ID}"