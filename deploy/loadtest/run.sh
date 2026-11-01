#!/usr/bin/env bash
# make loadtest: drive the local prod profile with k6 and record what the box would see.
#
#   1. recreate the api with compose.loadtest.yml (the 2 s fake model; also clears the
#      in-memory rate-limit table so the probe starts from zero),
#   2. sample the api's RSS (VmRSS of uvicorn) and cgroup memory every second,
#   3. run `harnesswalk sweep` every 10 s inside the api while k6 runs (the TTL sweeper under
#      load; the background sweeper runs as configured too),
#   4. run loadtest.js, then print one summary: k6's lines, RSS baseline and peak, sweeps.
#
# Needs ORIGIN_VERIFY and PROD_LOCAL_ENV in the environment (the Makefile passes both) and k6.
# Extra compose files: LOADTEST_COMPOSE_EXTRA="-f path.yml". Results: $LOADTEST_OUT (a temp dir
# by default): k6-summary.json, rss.csv, sweeps.log.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${ORIGIN_VERIFY:?run through make loadtest (it reads the local prod env file)}"
: "${PROD_LOCAL_ENV:?run through make loadtest}"
command -v k6 >/dev/null || { echo "k6 is missing: brew install k6" >&2; exit 1; }

OUT="${LOADTEST_OUT:-$(mktemp -d)}"
mkdir -p "$OUT"
# shellcheck disable=SC2206
compose=(docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.prod-local.yml
  -f deploy/loadtest/compose.loadtest.yml ${LOADTEST_COMPOSE_EXTRA:-}
  --env-file "$PROD_LOCAL_ENV" --profile prod)

echo "loadtest: recreating the api with the load-test fixture"
"${compose[@]}" up -d --force-recreate --no-build api-prod caddy >/dev/null 2>&1
cid="$("${compose[@]}" ps -q api-prod)"
for _ in $(seq 1 60); do
  curl -fsk --resolve origin.harnesswalk.vynci.dev:8443:127.0.0.1 -H "X-Origin-Verify: $ORIGIN_VERIFY" \
    -o /dev/null https://origin.harnesswalk.vynci.dev:8443/healthz && break
  sleep 1
done

rss() { docker exec "$cid" sh -c "awk '/VmRSS/ {print \$2}' /proc/1/status" 2>/dev/null || echo 0; }
cgroup() { docker stats --no-stream --format '{{.MemUsage}}' "$cid" 2>/dev/null | awk '{print $1}'; }
echo "t,vmrss_kib,cgroup" >"$OUT/rss.csv"
baseline="$(rss)"
(
  t=0
  while :; do echo "$t,$(rss),$(cgroup)" >>"$OUT/rss.csv"; sleep 1; t=$((t + 1)); done
) &
sampler=$!

if docker exec "$cid" harnesswalk sweep --help >/dev/null 2>&1; then
  (
    while :; do
      s=$(date +%s)
      # The same fixture directory as the server (compose.loadtest.yml): the CLI builds the
      # providers too, and HARNESSWALK_FIXTURE names the load-test fixture.
      if docker exec "$cid" harnesswalk sweep --set models.providers.fake.fixture_dir=/loadtest/fixtures \
        >>"$OUT/sweeps.log" 2>&1; then r=ok; else r=failed; fi
      echo "sweep $r in $(( $(date +%s) - s )) s" >>"$OUT/sweeps.log"
      sleep 10
    done
  ) &
  sweeper=$!
else
  echo "harnesswalk sweep is not in this image yet (p6-public); only the background sweeper, if any" >"$OUT/sweeps.log"
  sweeper=""
fi

set +e
LOADTEST_OUT="$OUT" k6 run --quiet deploy/loadtest/loadtest.js 2>"$OUT/k6.log"
status=$?
set -e
kill "$sampler" ${sweeper:+"$sweeper"} 2>/dev/null || true
wait 2>/dev/null || true

grep -h 'rate_limit:' "$OUT/k6.log" | sed 's/.*msg="\(.*\)".*/\1/; s/\\"/"/g' || true
peak="$(tail -n +2 "$OUT/rss.csv" | cut -d, -f2 | sort -n | tail -1)"
peak_cg="$(tail -n +2 "$OUT/rss.csv" | cut -d, -f3 | sort -h | tail -1)"
echo "api VmRSS: baseline $((baseline / 1024)) MiB, peak $((${peak:-0} / 1024)) MiB (cgroup peak $peak_cg, limit 1.5 GiB)"
echo "sweeps: $(grep -c '^sweep ok' "$OUT/sweeps.log" || true) ok, $(grep -c '^sweep failed' "$OUT/sweeps.log" || true) failed$([ -z "$sweeper" ] && echo ' (sweep CLI not present)')"
echo "results in $OUT"
exit "$status"
