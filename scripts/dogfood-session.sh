#!/usr/bin/env bash
# Shared SaaS UI session helpers — plutus_sid cookie + CSRF for dogfood curls.
# Usage: source "$(dirname "$0")/dogfood-session.sh"
#        dogfood_session_login "$BASE" "$API_KEY"

dogfood_session_login() {
  local base="$1"
  local api_key="$2"

  PLUTUS_DOGFOOD_COOKIE_JAR="${PLUTUS_DOGFOOD_COOKIE_JAR:-$(mktemp)}"
  export PLUTUS_DOGFOOD_COOKIE_JAR

  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" \
    -c "$PLUTUS_DOGFOOD_COOKIE_JAR" -b "$PLUTUS_DOGFOOD_COOKIE_JAR" \
    -X POST "$base/ui/saas/login" -d "api_token=${api_key}")
  if [[ "$code" != "303" ]]; then
    echo "dogfood login failed HTTP $code" >&2
    return 1
  fi

  local html
  html=$(curl -sf -b "$PLUTUS_DOGFOOD_COOKIE_JAR" "$base/ui/saas/app")
  PLUTUS_DOGFOOD_CSRF=$(
    echo "$html" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -1
  )
  export PLUTUS_DOGFOOD_CSRF
  if [[ -z "$PLUTUS_DOGFOOD_CSRF" ]]; then
    echo "dogfood login: missing csrf_token on dashboard" >&2
    return 1
  fi
}

# Authenticated GET (session cookie only).
dogfood_ui_get() {
  curl "$@" -b "${PLUTUS_DOGFOOD_COOKIE_JAR}"
}

# Authenticated POST with CSRF (adds -F csrf_token= when -F/-d present).
dogfood_ui_post() {
  local args=()
  local has_body=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -F|--form|-d|--data|--data-binary|--data-urlencode)
        has_body=1
        args+=("$1" "$2")
        shift 2
        ;;
      *)
        args+=("$1")
        shift
        ;;
    esac
  done
  if [[ "$has_body" -eq 1 && -n "${PLUTUS_DOGFOOD_CSRF:-}" ]]; then
    args+=(-F "csrf_token=${PLUTUS_DOGFOOD_CSRF}")
  fi
  curl -b "${PLUTUS_DOGFOOD_COOKIE_JAR}" -c "${PLUTUS_DOGFOOD_COOKIE_JAR}" "${args[@]}"
}