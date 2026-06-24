#!/usr/bin/env bash
# Wire Plutus SaaS to Cloudflare R2 (S3-compatible tenant gallery storage).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
ACCOUNT_ID="${R2_ACCOUNT_ID:?Set R2_ACCOUNT_ID}"
ACCESS_KEY="${R2_ACCESS_KEY_ID:?Set R2_ACCESS_KEY_ID}"
SECRET_KEY="${R2_SECRET_ACCESS_KEY:?Set R2_SECRET_ACCESS_KEY}"
BUCKET="${PLUTUS_S3_BUCKET:-plutus-tenant-galleries}"
ENDPOINT="${PLUTUS_S3_ENDPOINT:-https://${ACCOUNT_ID}.r2.cloudflarestorage.com}"

echo "==> Ensure boto3"
if [[ -d "$ROOT/.venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  pip install -q '.[s3]'
else
  pip install -q boto3
fi

echo "==> Verify R2 bucket access ($BUCKET)"
ENDPOINT="$ENDPOINT" BUCKET="$BUCKET" ACCESS_KEY="$ACCESS_KEY" SECRET_KEY="$SECRET_KEY" \
python3 - <<'PY'
import os
import boto3
from botocore.exceptions import ClientError

client = boto3.client(
    "s3",
    endpoint_url=os.environ["ENDPOINT"],
    aws_access_key_id=os.environ["ACCESS_KEY"],
    aws_secret_access_key=os.environ["SECRET_KEY"],
    region_name="auto",
)
bucket = os.environ["BUCKET"]
try:
    client.head_bucket(Bucket=bucket)
except ClientError:
    client.create_bucket(Bucket=bucket)
print("bucket ready:", bucket)
PY

python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {
    "PLUTUS_STORAGE_BACKEND": "s3",
    "PLUTUS_S3_BUCKET": "${BUCKET}",
    "PLUTUS_S3_REGION": "auto",
    "PLUTUS_S3_ENDPOINT": "${ENDPOINT}",
    "PLUTUS_S3_ACCESS_KEY": "${ACCESS_KEY}",
    "PLUTUS_S3_SECRET_KEY": "${SECRET_KEY}",
    "PLUTUS_S3_PREFIX": "plutus/tenants",
}
lines = env_path.read_text().splitlines() if env_path.exists() else []
out, seen = [], set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_path.write_text("\\n".join(out).rstrip() + "\\n")
print("wrote R2 settings to", env_path)
PY

if systemctl --user is-active plutus-saas >/dev/null 2>&1; then
  echo "==> Restart plutus-saas"
  systemctl --user restart plutus-saas
  sleep 2
  curl -sf http://127.0.0.1:8031/healthz | python3 -c "
import json, sys
storage = json.load(sys.stdin)['checks']['storage']
print('  storage:', storage)
assert storage.get('backend') == 's3' and storage.get('configured'), storage
"
fi

echo "Done — R2 wired (endpoint ${ENDPOINT})"