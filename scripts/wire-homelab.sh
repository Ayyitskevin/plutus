#!/usr/bin/env bash
# Wire Plutus homelab :8030 — Stripe keys from mise-flow-sync/.env
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${PLUTUS_ENV_FILE:-$ROOT/.env.homelab}"
MISE_ENV="${MISE_STRIPE_ENV:-$HOME/ai-workspace/mise-flow-sync/.env}"

python3 - <<PY
from pathlib import Path

env_path = Path("${ENV_FILE}")
mise_path = Path("${MISE_ENV}")

def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out

def upsert(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.write_text("\\n".join(out).rstrip() + "\\n")

mise = parse_env(mise_path)
updates: dict[str, str] = {}
if sk := mise.get("MISE_STRIPE_SECRET_KEY"):
    updates["STRIPE_SECRET_KEY"] = sk
if wh := mise.get("MISE_STRIPE_WEBHOOK_SECRET"):
    updates["STRIPE_WEBHOOK_SECRET"] = wh
if not updates:
    raise SystemExit(f"No Stripe keys found in {mise_path}")
upsert(env_path, updates)
print("wrote homelab Stripe keys to", env_path)
for k in updates:
    print(f"  {k}=***")
PY

echo "==> Restart plutus-homelab"
systemctl --user restart plutus-homelab
sleep 2
curl -sf http://127.0.0.1:8030/healthz | python3 -c "
import json,sys
h=json.load(sys.stdin)
b=h['checks']['billing']
print('  status:', h['status'])
print('  billing:', b)
assert b.get('reachable'), 'Stripe still unreachable'
"