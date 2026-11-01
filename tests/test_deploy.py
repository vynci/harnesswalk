"""The API image builds the web app reproducibly and never bakes local secrets into it; the origin
proxy hands the API one viewer address it can trust."""

import re

from tests.spec.support import ROOT

DOCKERFILE = (ROOT / "deploy" / "api.Dockerfile").read_text(encoding="utf-8")
WEB_STAGE = DOCKERFILE.split("FROM python:")[0]


def test_web_stage_pins_pnpm_and_never_prompts() -> None:
    assert "COREPACK_ENABLE_DOWNLOAD_PROMPT=0" in WEB_STAGE
    assert re.search(r"^ARG PNPM_VERSION=\d+\.\d+\.\d+$", WEB_STAGE, re.M)
    # Every pnpm install names the pinned version, never a floating major like pnpm@10.
    installs = re.findall(r"pnpm@(\S+)", WEB_STAGE)
    assert installs and all(v.startswith("${PNPM_VERSION}") for v in installs), installs


def test_web_stage_fails_when_the_build_leaves_no_app() -> None:
    run = WEB_STAGE[WEB_STAGE.index("RUN if [ -f package.json ]") :]
    built, _, placeholder = run.partition("else")
    assert "test -f dist/index.html" in built
    assert "mkdir -p dist" not in built  # an empty dist only when web/ has no package.json
    assert "mkdir -p dist" in placeholder


def test_dockerignore_keeps_env_files_out_of_every_directory() -> None:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "**/.env*" in lines  # web/.env.local would otherwise bake VITE_* values into the app
    assert not any(line.startswith("!") and ".env" in line for line in lines)


def test_caddy_gives_the_api_one_trusted_viewer_address() -> None:
    """The per-IP rate limit keys on what Caddy forwards (HARNESSWALK_TRUST_PROXY=1). The API reads
    CloudFront-Viewer-Address before X-Forwarded-For, so Caddy must drop a viewer-sent one and
    write X-Forwarded-For itself from the address it took from the viewer-ip function's header."""
    caddy = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    proxy = caddy[caddy.index("reverse_proxy api:8000") :].split("flush_interval", 1)[0]
    assert re.search(r"^\s*header_up X-Forwarded-For \{client_ip\}\s*$", proxy, re.M)
    assert re.search(r"^\s*header_up -CloudFront-Viewer-Address\s*$", proxy, re.M)
    assert re.search(r"^\s*header_up -X-Origin-Verify\s*$", proxy, re.M)
    first = re.search(r"client_ip_headers (\S+)", caddy)
    assert first and first.group(1) == "X-Harnesswalk-Viewer-Ip"
    assert "trusted_proxies static" in caddy  # the peer is always our distribution (SG + header)
