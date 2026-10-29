# deploy

Docker Compose for the learner path. The API image is built from `deploy/api.Dockerfile` in two
stages: a `node:24` stage builds `web/` with pnpm when it has a `package.json` (and is skipped
while `web/` is a placeholder), then the uv stage copies `web/dist` in, so the api serves the
web app at `/` (non-root user, `harnesswalk serve`). Image tags are pinned: `pgvector/pgvector:0.8.7-pg17` and
`ollama/ollama:0.40.8`. The public sandbox deployment arrives in P6.

## Profiles

| Profile | Services | Model | Use it when |
|---|---|---|---|
| `dev` | `api`, `postgres` (pgvector), `ollama` | Local Ollama container | Learning on a laptop |
| `homelab` | `api`, `postgres` | An Ollama or vLLM server elsewhere, set `HARNESSWALK_OLLAMA_URL` | A GPU box on your network serves the model |

```sh
docker compose -f deploy/docker-compose.yml --profile dev up --build      # or: make up
docker compose -f deploy/docker-compose.yml --profile homelab up --build
```

The api container listens on `127.0.0.1:8000`, keeps its SQLite stores in the `harness-data`
volume (`/data`), and runs as the non-root user `harnesswalk`. The `dev` profile starts an
empty Ollama; pull the model once with
`docker compose -f deploy/docker-compose.yml exec ollama ollama pull llama3.1:8b`. To try the
stack without a model, set `HARNESSWALK_PROVIDER=fake` and `HARNESSWALK_FIXTURE=plain_answer`.

Without Docker, `make run` starts the same API from the working tree, and serves `web/dist`
when `make web-build` has built it.

On macOS, Ollama in a container cannot use the GPU. Running Ollama natively and pointing
`HARNESSWALK_OLLAMA_URL` at `http://host.docker.internal:11434` with the `homelab` profile is
usually faster.

## Rules

- No secrets in compose files. Keys come from the environment (`.env.example` lists the names).
- Ports bind to `127.0.0.1` only. Exposing the stack is a P6 decision with TLS in front.
- The Postgres password default is for a local laptop only. Set `POSTGRES_PASSWORD` anywhere
  else.
- Data lives in named volumes (`pgdata`, `harness-data`, `ollama-models`).

## Later

A `sandbox` profile (P6) adds the reverse proxy, the public-mode limits and the TTL cleaner for
aiharness.vynci.dev.
