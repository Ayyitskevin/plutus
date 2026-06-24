#!/usr/bin/env bash
# Suite loop: Mise gallery → Argus vision → Plutus offer → Mnemosyne share CTA.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" 2>/dev/null || true

for ENV_FILE in \
  "${PLUTUS_ENV_FILE:-$ROOT/.env}" \
  "$ROOT/../argus/.env" \
  "$ROOT/../mnemosyne/.env"
do
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
done

GALLERY_ID="${1:-${MISE_GALLERY_ID:-1}}"
MNEMOSYNE_ALBUM="${MNEMOSYNE_ALBUM_ID:-}"

ARGS=(--gallery-id "$GALLERY_ID")
if [[ -n "$MNEMOSYNE_ALBUM" ]]; then
  ARGS+=(--mnemosyne-album-id "$MNEMOSYNE_ALBUM")
fi
if [[ "${PLUTUS_SUITE_PLUTUS_ONLY:-}" == "1" || "${PLUTUS_SUITE_PLUTUS_ONLY:-}" == "true" ]]; then
  ARGS+=(--plutus-only)
fi

exec python3 "$ROOT/scripts/dogfood_suite_loop.py" "${ARGS[@]}"