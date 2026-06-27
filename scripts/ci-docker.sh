#!/usr/bin/env bash
# Build the studio image and smoke-test /healthz inside a throwaway container.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="${PLUTUS_DOCKER_IMAGE:-plutus:ci}"
PORT="${PLUTUS_DOCKER_SMOKE_PORT:-18030}"
CONTAINER="plutus-ci-smoke-$$"
DATA_DIR=$(mktemp -d)
trap 'docker rm -f "$CONTAINER" 2>/dev/null || true; rm -rf "$DATA_DIR"' EXIT

if ! command -v docker >/dev/null 2>&1; then
  echo "ci-docker: docker not found" >&2
  exit 1
fi

echo "==> docker build $IMAGE"
docker build -t "$IMAGE" .

echo "==> run container on :$PORT"
docker run -d --name "$CONTAINER" \
  -p "127.0.0.1:${PORT}:8030" \
  -e PLUTUS_API_TOKEN=ci-admin-token \
  -e PLUTUS_PUBLIC_URL="http://127.0.0.1:${PORT}" \
  -v "${DATA_DIR}:/data" \
  "$IMAGE"

for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "==> /healthz"
HEALTH=$(curl -sf "http://127.0.0.1:${PORT}/healthz")
python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert d.get('service')=='plutus', d; assert d.get('studio_mode') is True, d" "$HEALTH"

echo "ci-docker: OK ($IMAGE)"