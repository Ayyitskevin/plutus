#!/usr/bin/env bash
# Deploy Plutus SaaS to Fly.io (secrets from .env, persistent /data volume).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FLYCTL="${FLYCTL:-flyctl}"
if ! command -v "$FLYCTL" >/dev/null 2>&1; then
  if [[ -x "$HOME/.fly/bin/flyctl" ]]; then
    FLYCTL="$HOME/.fly/bin/flyctl"
  else
    echo "flyctl not found — install: curl -fsSL https://fly.io/install.sh | sh" >&2
    exit 1
  fi
fi

APP="${FLY_APP:-plutus}"
REGION="${FLY_REGION:-iad}"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE" >&2
  exit 1
fi

if ! "$FLYCTL" auth whoami >/dev/null 2>&1; then
  echo "Not logged in to Fly — run: $FLYCTL auth login" >&2
  exit 1
fi

bash "$ROOT/scripts/validate-env.sh" "$ENV_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ "${PLUTUS_S3_ENDPOINT:-}" == http://127.0.0.1:* ]]; then
  echo "ERROR: .env still points S3 at local MinIO — wire production object storage first" >&2
  exit 1
fi

if [[ -z "${PLUTUS_SAAS_PUBLIC_URL:-}" ]]; then
  echo "WARN: PLUTUS_SAAS_PUBLIC_URL unset — set to https://${APP}.fly.dev before deploy" >&2
fi

echo "==> Ensure Fly app $APP"
if ! "$FLYCTL" apps list --json | python3 -c "
import json, sys
apps = {a['Name'] for a in json.load(sys.stdin)}
sys.exit(0 if '${APP}' in apps else 1)
" 2>/dev/null; then
  "$FLYCTL" apps create "$APP" --org personal
fi

echo "==> Ensure volume plutus_data ($REGION)"
if ! "$FLYCTL" volumes list -a "$APP" --json | python3 -c "
import json, sys
names = {v.get('Name') for v in json.load(sys.stdin)}
sys.exit(0 if 'plutus_data' in names else 1)
" 2>/dev/null; then
  "$FLYCTL" volumes create plutus_data --region "$REGION" --size 1 -a "$APP" -y
fi

echo "==> Sync secrets from $ENV_FILE"
TMP_SECRETS=$(mktemp)
trap 'rm -f "$TMP_SECRETS"' EXIT

python3 - <<'PY' "$ENV_FILE" "$TMP_SECRETS"
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
skip_keys = {
    "PLUTUS_PORT",
    "PLUTUS_HOST",
    "PLUTUS_DATA_DIR",
}
lines_out = []
for line in env_path.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key in skip_keys:
        continue
    if not value.strip():
        continue
    lines_out.append(f"{key}={value}")
out_path.write_text("\n".join(lines_out) + "\n")
PY

"$FLYCTL" secrets import -a "$APP" < "$TMP_SECRETS"

echo "==> Deploy"
"$FLYCTL" deploy -a "$APP" --ha=false

echo ""
echo "Done — https://${APP}.fly.dev (set PLUTUS_SAAS_PUBLIC_URL to your custom domain when ready)"