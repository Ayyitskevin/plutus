#!/usr/bin/env bash
# Phase 5 dogfood: upload → Argus grok vision → Plutus recommend
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-session.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-wait-batch.sh"

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"
DEMO_DIR="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"
LIMIT="${PLUTUS_ARGUS_ANALYZE_LIMIT:-2}"

if [[ ! -d "$DEMO_DIR" ]]; then
  echo "Demo gallery not found: $DEMO_DIR" >&2
  exit 1
fi

echo "==> Health"
curl -sf "$BASE/healthz" | python3 -m json.tool | head -30

PLUTUS_DOGFOOD_ROOT="$ROOT"
echo "==> Dogfood tenant"
SLUG="p5-$(date +%s | tail -c 6)"
dogfood_bootstrap_tenant "$SLUG" "Phase5 Studio"
API_KEY="$PLUTUS_DOGFOOD_API_KEY"
echo "  tenant=$SLUG key=${API_KEY:0:24}..."
dogfood_session_login "$BASE" "$API_KEY"

echo "==> Upload ${LIMIT} demo photos from $DEMO_DIR"
FILES=()
for img in $(find "$DEMO_DIR" -maxdepth 1 -name '*.jpg' | sort | head -n "$LIMIT"); do
  FILES+=(-F "files=@${img}")
done
UPLOAD=$(dogfood_ui_post -sf -X POST "$BASE/ui/saas/app/upload" \
  -F "gallery_name=Phase5 dogfood" \
  -F "analyze=1" \
  "${FILES[@]}" \
  -D - -o /dev/null | grep -i '^location:' | awk '{print $2}' | tr -d '\r')
echo "  redirect=$UPLOAD"
RUN_ID="${UPLOAD#/runs/}"
if [[ -z "$RUN_ID" || "$RUN_ID" == "$UPLOAD" ]]; then
  if [[ "$UPLOAD" == *"analyzing="* ]]; then
    BATCH_ID="${UPLOAD#*analyzing=}"
    BATCH_ID="${BATCH_ID%%&*}"
    dogfood_wait_batch "$BASE" "$API_KEY" "$BATCH_ID"
    RUN_ID="$DOGFOOD_RUN_ID"
  fi
fi
if [[ -z "$RUN_ID" || "$RUN_ID" == "$UPLOAD" ]]; then
  echo "Expected redirect to /runs/{id}, got: $UPLOAD" >&2
  exit 1
fi

echo "==> Run $RUN_ID summary"
curl -sf "$BASE/runs/$RUN_ID/json" -H "Authorization: Bearer $API_KEY" | python3 -c "
import json,sys
row=json.load(sys.stdin)
payload=row.get('payload') or {}
bundles=payload.get('bundles') or []
print('  bundles:', len(bundles))
print('  estimated_total_cents:', payload.get('estimated_total_cents'))
if bundles:
    items=bundles[0].get('items') or []
    if items:
        photo=items[0].get('photo') or {}
        print('  top photo keeper:', photo.get('keeper_score'))
        print('  top photo hero:', photo.get('hero_potential'))
"

echo "==> Phase 5 dogfood OK — run $RUN_ID"