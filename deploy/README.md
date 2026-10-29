# deploy

Docker Compose for the learner path. The API image is built from `deploy/api.Dockerfile` (uv,
non-root user, `harnesswalk serve`). Image tags are pinned: `pgvector/pgvector:0.8.7-pg17` and
`ollama/ollama:0.40.8`. The public sandbox deployment arrives in P6.

## Profiles

| Profile | Services | Model | Use it when |
|---|---|---|---|
| `dev` | `api`, `postgres` (pgvector), `ollama` | Local Ollama container | Learning on a laptop |
| `homelab` | `api`, `postgres` | An Ollama or vLLM server elsewhere, set `HARNESSWALK_OLLAMA_URL` | A GPU box on your network serves the model |

```sh
docker compose -f deploy/docker-compose.yml --profile dev up
docker compose -f deploy/docker-compose.yml --profile homelab up
```

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
