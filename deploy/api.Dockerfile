# harnesswalk API image: the reference harness served by uvicorn.
#
#   docker build -f deploy/api.Dockerfile -t harnesswalk-api .
#
# Built with uv from the lock file, runs as a non-root user, keeps state under /data.
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

RUN useradd --create-home --uid 10001 harnesswalk \
    && mkdir -p /data \
    && chown harnesswalk:harnesswalk /data

USER harnesswalk

ENV PATH="/app/.venv/bin:${PATH}" \
    HARNESSWALK_ROOT=/app \
    HARNESSWALK_DATA_DIR=/data

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

CMD ["harnesswalk", "serve", "--host", "0.0.0.0", "--port", "8000"]
