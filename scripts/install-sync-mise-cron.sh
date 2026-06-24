#!/usr/bin/env bash
# Install a user crontab entry to rsync published Mise originals before recommends.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYNC_SCRIPT="$ROOT/scripts/sync-mise-media.sh"
SCHEDULE="${MISE_SYNC_CRON:-0 */6 * * *}"
LOG_DIR="${MISE_SYNC_LOG_DIR:-$HOME/.local/log}"
LOG_FILE="$LOG_DIR/sync-mise-media.log"
MARKER="# plutus-sync-mise-media"

mkdir -p "$LOG_DIR"
chmod +x "$SYNC_SCRIPT"

ENTRY="${SCHEDULE} ${SYNC_SCRIPT} >>${LOG_FILE} 2>&1 ${MARKER}"

EXISTING="$(crontab -l 2>/dev/null || true)"
if echo "$EXISTING" | grep -Fq "$MARKER"; then
  echo "==> Cron already installed ($MARKER)"
  echo "$EXISTING" | grep -F "$MARKER" || true
  exit 0
fi

{
  echo "$EXISTING"
  echo "$ENTRY"
} | crontab -

echo "==> Installed cron: $ENTRY"
echo "    log: $LOG_FILE"