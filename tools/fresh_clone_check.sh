#!/usr/bin/env bash
# Fresh-clone check: does the README work for a stranger on a clean machine?
#
#   tools/fresh_clone_check.sh            # or: make fresh-clone
#   FRESH_CLONE_REF=main tools/fresh_clone_check.sh
#
# Bundles the committed history of this repository (FRESH_CLONE_REF, default HEAD; uncommitted
# changes are not part of a clone), starts a clean `python:3.12` container (no Ollama, no model
# keys, no caches from this machine), and inside it, as a non-root user:
#
#   1. installs the README's prerequisites: uv and Node 24 (pnpm comes from corepack);
#   2. clones the bundle and runs the README Quickstart word for word:
#      make setup && make web-build && make run PROVIDER=fake
#      then checks that http://127.0.0.1:8000 serves the app and the API answers a turn
#      on the fake provider (the trailhead_return fixture the Quickstart suggests);
#   3. runs the README's no-browser command and `make turn`;
#   4. runs `make test` and `make lint`.
#
# Any failed step exits non-zero with the step's name. Needs Docker and network access for the
# image, uv, Node and the Python and npm packages. FRESH_CLONE_KEEP=1 keeps the scratch
# directory; FRESH_CLONE_IMAGE picks another image.
set -euo pipefail

if [[ "${1:-}" != "--inside" ]]; then
  root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
  ref="${FRESH_CLONE_REF:-HEAD}"
  image="${FRESH_CLONE_IMAGE:-python:3.12}"
  work="$(mktemp -d "${TMPDIR:-/tmp}/harnesswalk-fresh-XXXXXX")"
  # shellcheck disable=SC2329 # called by the EXIT trap
  cleanup() { if [[ "${FRESH_CLONE_KEEP:-}" == 1 ]]; then echo "kept $work"; else rm -rf "$work"; fi; }
  trap cleanup EXIT
  if [[ "$(git -C "$root" rev-parse --is-shallow-repository)" == true ]]; then
    echo "fresh-clone: $root is a shallow clone; fetch the full history first (fetch-depth: 0)" >&2
    exit 2
  fi
  commit="$(git -C "$root" rev-parse --short "$ref")"
  if [[ -n "$(git -C "$root" status --porcelain)" ]]; then
    echo "fresh-clone: note: uncommitted changes in $root are not part of the clone"
  fi
  git -C "$root" bundle create "$work/repo.bundle" "$ref" 2>/dev/null
  cp "$0" "$work/fresh_clone_check.sh"
  # mktemp makes a 0700 directory; inside the container an unprivileged user must read the
  # bundle and this script through the bind mount (Linux hosts keep the mode, Docker Desktop
  # does not), so open it up: it holds nothing secret.
  chmod 755 "$work" && chmod 644 "$work"/*
  echo "fresh-clone: $commit in a clean $image container"
  docker run --rm --init \
    -v "$work:/in:ro" \
    -e FRESH_CLONE_COMMIT="$commit" \
    "$image" bash /in/fresh_clone_check.sh --inside
  exit $?
fi

# ---------------------------------------------------------------- inside the container
step() { printf '\n==> %s\n' "$*"; current="$*"; }
fail() { printf '\nFRESH CLONE FAILED at: %s\n' "$current" >&2; exit 1; }
trap fail ERR
started=$SECONDS

if [[ "$(id -u)" == 0 ]]; then
  step "prerequisites: uv and Node 24 (no Ollama, no keys)"
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]] || command -v ollama >/dev/null; then false; fi
  arch="$(uname -m)"; case "$arch" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; esac
  base="https://nodejs.org/dist/latest-v24.x"
  curl -fsSL "$base/SHASUMS256.txt" -o /tmp/node.sums
  tarball="$(grep -o "node-v24[^ ]*-linux-$arch.tar.gz" /tmp/node.sums | head -1)"
  curl -fsSL "$base/$tarball" -o "/tmp/$tarball"
  (cd /tmp && grep " $tarball\$" node.sums | sha256sum -c -)
  tar -xzf "/tmp/$tarball" -C /usr/local --strip-components=1
  corepack enable
  pip install --quiet --root-user-action=ignore "uv==${FRESH_CLONE_UV:-0.12.24}"
  echo "node $(node --version), uv $(uv --version | cut -d' ' -f2), python $(python3 --version | cut -d' ' -f2)"
  useradd --create-home --shell /bin/bash walker
  # Copy the inputs out of the read-only mount so the walker user can read them whatever the
  # host's permissions were, then run the rest as an ordinary user: the tool sandbox's limits
  # do not bind root.
  install -d -m 755 /opt/fresh && cp /in/repo.bundle /in/fresh_clone_check.sh /opt/fresh/ && chmod 644 /opt/fresh/*
  exec runuser -u walker -- env HOME=/home/walker FRESH_CLONE_COMMIT="${FRESH_CLONE_COMMIT:-}" \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 PATH="$PATH" bash /opt/fresh/fresh_clone_check.sh --inside
fi

cd "$HOME"
step "git clone (the bundle of ${FRESH_CLONE_COMMIT:-HEAD}) && cd harnesswalk"
git -c advice.detachedHead=false clone --quiet /opt/fresh/repo.bundle harnesswalk
cd harnesswalk
git switch --quiet -c main

step "README Quickstart: make setup"
make setup

step "README Quickstart: make web-build"
make web-build
test -f web/dist/index.html

step "README Quickstart: make run PROVIDER=fake, then the app and a turn on http://127.0.0.1:8000"
make run PROVIDER=fake >"$HOME/run.log" 2>&1 &
server=$!
for _ in $(seq 1 120); do
  curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break
  kill -0 "$server" 2>/dev/null || { cat "$HOME/run.log"; false; }
  sleep 0.5
done
curl -fsS http://127.0.0.1:8000/healthz
echo
curl -fsS http://127.0.0.1:8000/ -o "$HOME/index.html"
grep -qi '<div id="root"' "$HOME/index.html"
echo "the app is served at /"
uv run python - <<'PY'
import json

import httpx

base = "http://127.0.0.1:8000"
r = httpx.post(
    f"{base}/v1/turns",
    json={
        "message": "Can I return order A-1003, the Trailhead backpack?",
        "fixture": "trailhead_return",
    },
    timeout=10,
)
assert r.status_code == 202, (r.status_code, r.text)
events = []
with httpx.stream("GET", base + r.json()["events_url"], timeout=30) as s:
    data = []
    for line in s.iter_lines():
        if line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line and data:
            events.append(json.loads("\n".join(data)))
            data = []
            if events[-1]["type"] == "turn_end":
                break
end = events[-1]
assert end["type"] == "turn_end" and end["status"] == "done", end
reply = end["summary"]["reply"]
cited = end["summary"].get("citations") or []
nodes = sorted({e["node_id"] for e in events if e.get("node_id")})
assert reply and cited, end["summary"]
print(f"turn: {len(events)} events, {len(nodes)} nodes, {len(cited)} citation(s): {reply[:70]!r}")
PY
kill "$server" 2>/dev/null || true
pkill -f "harnesswalk serve" || true
wait "$server" 2>/dev/null || true

step "README: the no-browser turn"
out="$(uv run harnesswalk turn "Where is order A-1001?" --provider fake --fixture tool_call)"
echo "$out" | tail -4
grep -q "A-1001" <<<"$out"

step "README: make turn"
make turn | tail -3

step "make test"
make test

step "make lint"
make lint

printf '\nFRESH CLONE PASSED: %s in %ss\n' "${FRESH_CLONE_COMMIT:-HEAD}" "$((SECONDS - started))"
