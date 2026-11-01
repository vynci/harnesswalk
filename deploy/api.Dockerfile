# harnesswalk API image: the reference harness served by uvicorn, with the built web app.
#
#   docker build -f deploy/api.Dockerfile -t harnesswalk-api .
#   docker buildx build --platform linux/arm64,linux/amd64 -f deploy/api.Dockerfile \
#       --build-arg GIT_SHA=$(git rev-parse --short=12 HEAD) .   # multi-arch (build-push.sh)
#
# Two stages. The first builds web/ with pnpm when it has a package.json, and fails when that
# build leaves no dist/index.html; only while web/ is a placeholder (no package.json) does it
# leave an empty dist, so the API then serves no app. pnpm is pinned: the `packageManager` field
# of web/package.json wins when it is set, else PNPM_VERSION. Corepack never prompts, and
# .dockerignore keeps every .env file out of the context, so no local VITE_* value is baked in.
# The second stage is built with uv from the lock file, runs as a non-root user, keeps state
# under /data, and serves /app/web/dist at / when it holds an index.html.
#
# Multi-arch: linux/arm64 (the t4g origin, Apple silicon) and linux/amd64 (Intel Macs, CI). The
# web stage runs on the build machine's own platform (its output is the same on every arch), so
# a cross build only emulates the Python stage. The image also carries deploy/docker-compose.yml
# and deploy/caddy/Caddyfile: the origin takes them out of the image it deploys, so a rollback
# rolls them back with the code.
#
# Base images are pinned by digest (the multi-arch index, resolved 2026-11-01) as well as by
# tag, so a rebuild of an old commit gets the same bases. To move one: `docker buildx
# imagetools inspect <image:tag>` and paste the new index digest.

FROM --platform=$BUILDPLATFORM node:24-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS web
ARG PNPM_VERSION=10.33.0
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /web
COPY web/ ./
RUN if [ -f package.json ]; then \
        (corepack enable && corepack prepare pnpm@${PNPM_VERSION} --activate \
            || npm install -g pnpm@${PNPM_VERSION}) \
        && pnpm install --frozen-lockfile \
        && pnpm build \
        && (test -f dist/index.html \
            || (echo "pnpm build left no dist/index.html" >&2 && exit 1)); \
    else \
        echo "web/ has no package.json: building the API without the web app" \
        && mkdir -p dist; \
    fi

FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="harnesswalk-api" \
      org.opencontainers.image.description="The harnesswalk reference harness (uvicorn) with the built web app" \
      org.opencontainers.image.source="https://github.com/vynci/harnesswalk" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="${GIT_SHA}"

COPY --from=astral/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code edits do not reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --locked --no-dev --extra pgvector --no-install-project

COPY harnesswalk ./harnesswalk
COPY spec ./spec
COPY config ./config
COPY datasets ./datasets
COPY lessons ./lessons
RUN uv sync --locked --no-dev --extra pgvector
COPY --from=web /web/dist ./web/dist
COPY deploy/docker-compose.yml ./deploy/docker-compose.yml
COPY deploy/caddy/Caddyfile ./deploy/caddy/Caddyfile

RUN useradd --create-home --uid 10001 harnesswalk \
    && mkdir -p /data \
    && chown harnesswalk:harnesswalk /data

USER harnesswalk

ENV PATH="/app/.venv/bin:${PATH}" \
    HARNESSWALK_ROOT=/app \
    HARNESSWALK_DATA_DIR=/data \
    HARNESSWALK_WEB_DIST=/app/web/dist \
    HARNESSWALK_GIT_SHA=${GIT_SHA}

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

CMD ["harnesswalk", "serve", "--host", "0.0.0.0", "--port", "8000"]
