#!/usr/bin/env bash
# Dogfood tenant upload path against S3-backed storage.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/dogfood-session.sh"

HOST="${PLUTUS_HOST:-127.0.0.1}"
PORT="${PLUTUS_PORT:-8031}"
BASE="http://${HOST}:${PORT}"
DEMO_DIR="${PLUTUS_DOGFOOD_GALLERY:-$HOME/ai-workspace/argus/data/demo}"

if [[ ! -d "$DEMO_DIR" ]]; then
  echo "Demo gallery not found: $DEMO_DIR" >&2
  exit 1
fi

echo "==> Health (storage must be s3)"
curl -sf "$BASE/healthz" | python3 -c "
import json, sys
body = json.load(sys.stdin)
storage = body['checks']['storage']
print('  storage:', storage)
assert storage.get('backend') == 's3', storage
assert storage.get('configured'), storage
"

PLUTUS_DOGFOOD_ROOT="$ROOT"
echo "==> Dogfood tenant"
SLUG="s3-$(date +%s | tail -c 6)"
dogfood_bootstrap_tenant "$SLUG" "S3 Studio"
API_KEY="$PLUTUS_DOGFOOD_API_KEY"
echo "  tenant=$SLUG"
dogfood_session_login "$BASE" "$API_KEY"

echo "==> Upload one demo photo"
IMG=$(find "$DEMO_DIR" -maxdepth 1 -name '*.jpg' | sort | head -n 1)
UPLOAD=$(dogfood_ui_post -sf -X POST "$BASE/ui/saas/app/upload" \
  -F "gallery_name=S3 dogfood" \
  -F "files=@${IMG}" \
  -D - -o /dev/null | grep -i '^location:' | awk '{print $2}' | tr -d '\r')
echo "  redirect=$UPLOAD"

echo "==> S3 dogfood OK"