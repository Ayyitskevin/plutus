#!/usr/bin/env bash
# Wire Plutus SaaS to S3-compatible storage (local MinIO by default).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
COMPOSE="${PLUTUS_MINIO_COMPOSE:-$ROOT/ops/minio-compose.yml}"
BUCKET="${PLUTUS_S3_BUCKET:-plutus-tenant-galleries}"
ENDPOINT="${PLUTUS_S3_ENDPOINT:-http://127.0.0.1:9000}"
ACCESS_KEY="${PLUTUS_S3_ACCESS_KEY:-plutus}"
SECRET_KEY="${PLUTUS_S3_SECRET_KEY:-plutus-dev-secret}"

echo "==> Start MinIO (${COMPOSE})"
if command -v docker >/dev/null 2>&1; then
  MINIO_ROOT_USER="$ACCESS_KEY" MINIO_ROOT_PASSWORD="$SECRET_KEY" \
    docker compose -f "$COMPOSE" up -d
else
  echo "docker not found — configure PLUTUS_S3_* for R2/AWS manually" >&2
fi

echo "==> Ensure boto3 optional extra"
if [[ -d "$ROOT/.venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  pip install -q '.[s3]'
else
  pip install -q boto3
fi

echo "==> Wait for MinIO"
deadline=$((SECONDS + 30))
until curl -sf "$ENDPOINT/minio/health/live" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "MinIO not ready at $ENDPOINT" >&2
    exit 1
  fi
  sleep 1
done

echo "==> Create bucket $BUCKET (idempotent)"
ENDPOINT="$ENDPOINT" BUCKET="$BUCKET" ACCESS_KEY="$ACCESS_KEY" SECRET_KEY="$SECRET_KEY" \
python3 - <<'PY'
import os
import boto3
from botocore.exceptions import ClientError

endpoint = os.environ["ENDPOINT"]
bucket = os.environ["BUCKET"]
access = os.environ["ACCESS_KEY"]
secret = os.environ["SECRET_KEY"]

client = boto3.client(
    "s3",
    endpoint_url=endpoint,
    aws_access_key_id=access,
    aws_secret_access_key=secret,
    region_name="us-east-1",
)
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
    "PLUTUS_S3_REGION": "us-east-1",
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
print("wrote S3 settings to", env_path)
for key in updates:
    if "SECRET" in key or "KEY" in key:
        print(f"  {key}=***")
    else:
        print(f"  {key}={updates[key]}")
PY

if systemctl --user is-active plutus-saas >/dev/null 2>&1; then
  echo "==> Restart plutus-saas"
  systemctl --user restart plutus-saas
  sleep 2
  curl -sf http://127.0.0.1:8031/healthz | python3 -c "
import json, sys
body = json.load(sys.stdin)
storage = body['checks']['storage']
print('  storage:', storage)
assert storage.get('backend') == 's3' and storage.get('configured'), storage
"
fi

echo "Done — S3 wired (MinIO console: http://127.0.0.1:9001)"