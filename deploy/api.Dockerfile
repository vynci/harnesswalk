# harnesswalk API image: the reference harness served by uvicorn, with the built web app.
#
#   docker build -f deploy/api.Dockerfile -t harnesswalk-api .
#
# Two stages. The first builds web/ with pnpm when it has a package.json, and fails when that
# build leaves no dist/index.html; only while web/ is a placeholder (no package.json) does it
# leave an empty dist, so the API then serves no app. pnpm is pinned: the `packageManager` field
# of web/package.json wins when it is set, else PNPM_VERSION. Corepack never prompts, and
# .dockerignore keeps every .env file out of the context, so no local VITE_* value is baked in.
# The second stage is built with uv from the lock file, runs as a non-root user, keeps state
# under /data, and serves /app/web/dist at / when it holds an index.html.

FROM node:24-bookworm-slim AS web
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

FROM python:3.12-slim-bookworm

COPY --from=astral/uv:0.12.24 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code edits do not reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --locked --no-dev --no-install-project

COPY harnesswalk ./harnesswalk
COPY spec ./spec
COPY config ./config
COPY datasets ./datasets
RUN uv sync --locked --no-dev
COPY --from=web /web/dist ./web/dist

RUN useradd --create-home --uid 10001 harnesswalk \
    && mkdir -p /data \
    && chown harnesswalk:harnesswalk /data

USER harnesswalk

ENV PATH="/app/.venv/bin:${PATH}" \
    HARNESSWALK_ROOT=/app \
    HARNESSWALK_DATA_DIR=/data \
    HARNESSWALK_WEB_DIST=/app/web/dist

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

CMD ["harnesswalk", "serve", "--host", "0.0.0.0", "--port", "8000"]
