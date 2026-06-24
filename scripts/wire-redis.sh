#!/usr/bin/env bash
# Local Redis for SaaS rate limits (Docker) + PLUTUS_REDIS_URL in .env.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env}"
CONTAINER="${PLUTUS_REDIS_CONTAINER:-plutus-redis}"
PORT="${PLUTUS_REDIS_PORT:-6379}"
REDIS_URL="redis://127.0.0.1:${PORT}/0"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker required — install Docker or set PLUTUS_REDIS_URL manually" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
    echo "==> Starting existing ${CONTAINER}"
    docker start "${CONTAINER}" >/dev/null
  else
    echo "==> Creating ${CONTAINER} on 127.0.0.1:${PORT}"
    docker run -d \
      --name "${CONTAINER}" \
      --restart unless-stopped \
      -p "127.0.0.1:${PORT}:6379" \
      redis:7-alpine >/dev/null
  fi
fi

deadline=$((SECONDS + 15))
until docker exec "${CONTAINER}" redis-cli ping 2>/dev/null | grep -q PONG; do
  if (( SECONDS >= deadline )); then
    echo "Redis container did not become ready" >&2
    exit 1
  fi
  sleep 1
done

python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {
    "PLUTUS_REDIS_URL": "${REDIS_URL}",
    "PLUTUS_RATE_LIMIT_ENABLED": "true",
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
print("wrote Redis settings to", env_path)
for key in updates:
    print(f"  {key}={updates[key]}")
PY

if [[ -x "$ROOT/.venv/bin/pip" ]]; then
  "$ROOT/.venv/bin/pip" install -q 'redis>=5,<6' 2>/dev/null || true
fi

if systemctl --user is-active plutus-saas >/dev/null 2>&1; then
  echo "==> Restart plutus-saas"
  systemctl --user restart plutus-saas
  sleep 2
fi

curl -sf "http://127.0.0.1:${PLUTUS_PORT:-8031}/healthz" | python3 -c "
import json, sys
h = json.load(sys.stdin)
redis = h['checks'].get('redis', {})
print('  redis:', redis)
assert redis.get('reachable') is True, redis
"

echo "Done — Redis rate limits armed (${REDIS_URL})"