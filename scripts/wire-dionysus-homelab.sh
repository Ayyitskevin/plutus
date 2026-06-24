#!/usr/bin/env bash
# Arm Plutus homelab pitch enrichment via local Dionysus (:8450).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIO_ROOT="${DIONYSUS_ROOT:-$HOME/ai-workspace/dionysus}"
ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}"
TOKEN="${MISE_FLEET_TOKEN:-6e885f7b784c62b27e08be293da85108d13c1afbc0a02bfaa0bc97d9786fb57d}"
ORG_SLUG="${PLUTUS_DIONYSUS_ORG_SLUG:-blue-plate}"
DIO_URL="${PLUTUS_DIONYSUS_URL:-http://127.0.0.1:8450}"

echo "==> Ensure Dionysus demo workspace"
cd "$DIO_ROOT"
# shellcheck disable=SC1091
source .venv/bin/activate
export DIONYSUS_DATA_DIR="${DIONYSUS_DATA_DIR:-$DIO_ROOT/data}"
python -m app.cli migrate
python -m app.cli seed-demo >/dev/null

if ! systemctl --user is-active dionysus-homelab >/dev/null 2>&1; then
  if [[ -f "$ROOT/ops/dionysus-homelab-user.service" ]]; then
    mkdir -p "$HOME/.config/systemd/user"
    cp "$ROOT/ops/dionysus-homelab-user.service" "$HOME/.config/systemd/user/dionysus-homelab.service"
    systemctl --user daemon-reload
    systemctl --user enable --now dionysus-homelab
  else
    echo "start Dionysus manually on :8450" >&2
    exit 1
  fi
fi

deadline=$((SECONDS + 20))
until curl -sf "$DIO_URL/readiness" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "Dionysus not ready at $DIO_URL" >&2
    exit 1
  fi
  sleep 1
done

echo "==> Wire $ENV_FILE"
python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
updates = {
    "PLUTUS_DIONYSUS_URL": "${DIO_URL}",
    "PLUTUS_DIONYSUS_TOKEN": "${TOKEN}",
    "PLUTUS_DIONYSUS_ORG_SLUG": "${ORG_SLUG}",
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
print("wrote Dionysus pitch settings to", env_path)
PY

echo "==> Restart plutus-homelab"
systemctl --user restart plutus-homelab
sleep 2
curl -sf http://127.0.0.1:8030/healthz | python3 -c "
import json, sys
body = json.load(sys.stdin)
dio = body['checks'].get('dionysus', {})
print('  dionysus:', dio)
assert dio.get('configured'), dio
assert dio.get('status') == 'ok', dio
"

echo "Done — Dionysus pitch armed for homelab (org=${ORG_SLUG})"