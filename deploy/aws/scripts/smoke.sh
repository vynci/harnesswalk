#!/usr/bin/env bash
# Smoke the public site: /healthz, /readyz, /v1/capabilities, and one fake-small turn followed
# to its turn_end over SSE. Exit 1 when any check fails. SMOKE_MODEL picks the turn's alias.
#
#   deploy/aws/scripts/smoke.sh                                  # https://harnesswalk.vynci.dev
#   BASE_URL=https://origin.harnesswalk.vynci.dev:8443 SMOKE_INSECURE=1 \
#     SMOKE_RESOLVE=origin.harnesswalk.vynci.dev:8443:127.0.0.1 ORIGIN_VERIFY=... smoke.sh
#                                                                 # the local prod profile
# Read only apart from the one turn (which lands in a fresh anonymous tenant that the TTL
# sweeper deletes). ORIGIN_VERIFY, when set, is sent as X-Origin-Verify (direct-to-origin runs
# only; through CloudFront it is added at the edge) and never printed.
source "$(dirname "$0")/lib.sh"
need curl jq

BASE_URL="${BASE_URL:-https://$HW_DOMAIN}"
# Connection options shared by every request (bash 3.2 safe: never expand an empty array).
opts=(--silent --show-error)
if [ "${SMOKE_INSECURE:-0}" = "1" ]; then opts+=(--insecure); fi
if [ -n "${SMOKE_RESOLVE:-}" ]; then opts+=(--resolve "$SMOKE_RESOLVE"); fi
if [ -n "${ORIGIN_VERIFY:-}" ]; then opts+=(-H "X-Origin-Verify: $ORIGIN_VERIFY"); fi
jar="$(mktemp)"
trap 'rm -f "$jar"' EXIT
failed=0
ok() { log "PASS $*"; }
bad() { log "FAIL $*"; failed=1; }
get() { curl "${opts[@]}" --max-time 20 -b "$jar" -c "$jar" "$@"; }

log "smoke $BASE_URL"
# probe PATH: 200 with a JSON body. (A bare 200 is not enough: the API answers unknown paths
# with the web app's index.html.)
probe() {
  local out code body
  out="$(get -w '\n%{http_code}' "$BASE_URL$1" || true)"
  code="$(tail -n1 <<<"$out")"
  body="$(sed '$d' <<<"$out")"
  if [ "$code" = "200" ] && jq -e . <<<"$body" >/dev/null 2>&1; then
    ok "$1 200 $(jq -c '{status} + (if .version then {version} else {} end)' <<<"$body" 2>/dev/null)"
  else
    bad "$1 $code $(head -c 80 <<<"$body" | tr '\n' ' ')"
  fi
}
probe /healthz
probe /readyz

caps="$(get "$BASE_URL/v1/capabilities" || true)"
mode="$(jq -r '.mode // empty' <<<"$caps" 2>/dev/null || true)"
[ "$mode" = "public" ] && ok "capabilities mode=public" || bad "capabilities mode=${mode:-<none>}"
models="$(jq -r '[.public.models[]? | "\(.alias)\(if .available then "" else "(unavailable:\(.reason))" end)"] | join(" ")' <<<"$caps" 2>/dev/null || true)"
if [ -n "$models" ]; then
  ok "public models: $models"
  jq -e '.public.models[] | select(.alias=="fake-small" and .available)' <<<"$caps" >/dev/null \
    && ok "fake-small available" || bad "fake-small not available"
else
  bad "capabilities has no public.models (is the p6-public runtime deployed?)"
fi

# SMOKE_MODEL: the alias the turn asks for (default fake-small; empty sends no model).
model="${SMOKE_MODEL-fake-small}"
payload="$(jq -cn --arg m "$model" '{message: "What are your support hours?"} + (if $m == "" then {} else {model: $m} end)')"
start="$(get -w '\n%{http_code}' -H 'Content-Type: application/json' -d "$payload" "$BASE_URL/v1/turns" || true)"
code="$(tail -n1 <<<"$start")"
body="$(sed '$d' <<<"$start")"
turn="$(jq -r '.turn_id // empty' <<<"$body" 2>/dev/null || true)"
if [ "$code" = "202" ] && [ -n "$turn" ]; then
  ok "POST /v1/turns 202 ($turn)"
  stream="$(curl "${opts[@]}" --max-time 60 -N -b "$jar" \
    -H 'Accept: text/event-stream' "$BASE_URL/v1/turns/$turn/events" \
    | awk '{ print } /^event: turn_end/ { getline; print; exit }' || true)"
  events="$(grep -c '^event: ' <<<"$stream" || true)"
  if grep -q '^event: turn_end' <<<"$stream"; then
    status="$(grep -A1 '^event: turn_end' <<<"$stream" | sed -n 's/^data: //p' | jq -r '.status // empty')"
    ok "SSE $events events, turn_end status=${status:-?}"
  else
    bad "SSE ended without turn_end after $events events"
  fi
else
  bad "POST /v1/turns $code $(jq -c '.error // empty' <<<"$body" 2>/dev/null || true)"
fi

[ "$failed" = "0" ] && log "smoke passed" || log "smoke FAILED"
exit "$failed"
