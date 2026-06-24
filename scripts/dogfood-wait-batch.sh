#!/usr/bin/env bash
# Poll /upload-batches/{id}/status until run_id is ready (async Argus analyze).
# Usage: dogfood_wait_batch BASE API_KEY BATCH_ID [timeout_seconds]
dogfood_wait_batch() {
  local base="$1" api_key="$2" batch_id="$3"
  local timeout="${4:-600}"
  local deadline=$((SECONDS + timeout))
  local run_id=""

  echo "==> Waiting for batch ${batch_id} (timeout ${timeout}s)"
  while (( SECONDS < deadline )); do
    local status_json
    status_json=$(curl -sf "${base}/upload-batches/${batch_id}/status" \
      -H "Authorization: Bearer ${api_key}")
    run_id=$(echo "$status_json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d.get('failed'):
    raise SystemExit(d.get('analyze_error') or 'analyze failed')
if d.get('done') and d.get('run_id'):
    print(d['run_id'])
")
    if [[ -n "$run_id" ]]; then
      echo "  run_id=$run_id"
      DOGFOOD_RUN_ID="$run_id"
      export DOGFOOD_RUN_ID
      return 0
    fi
    sleep 3
  done
  echo "batch ${batch_id} not ready after ${timeout}s" >&2
  return 1
}