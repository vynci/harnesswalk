# How the web app is wired into the repository

The P2 integration pass wired everything this file used to list as to-do. What is where:

## Make targets (repository root)

| Target | Runs |
|---|---|
| `make web-build` | `pnpm install --frozen-lockfile` (once per lock change), then `pnpm build` into `web/dist` |
| `make run` | The API on 127.0.0.1:8000, serving `web/dist` at `/` when it is built |
| `make web-dev` | Vite on 127.0.0.1:5173, proxying `/v1` and `/healthz` to `HARNESSWALK_API_URL` |
| `make web-mock` | Vite on the in-browser mock (`pnpm dev:mock`) |
| `make web-test` | `pnpm lint` (eslint, then `tsc -b`) and `pnpm test` (Vitest) |
| `make web-e2e-mock` | Playwright smoke tests against the mock build |
| `make e2e` | Playwright against `harnesswalk serve --provider fake` on a scratch port |
| `make screenshots` | The live suite's screenshot tests, writing `docs/screenshots/` |

## CI

`.github/workflows/ci.yml` has a `web` job (install, lint, unit tests, build) and an `e2e` job
(uv sync, pnpm install, Chromium with its system packages, the mock smoke tests, then the live
suite, which starts and stops the API itself).

## The Docker image

`deploy/api.Dockerfile` builds `web/` in a `node:24` stage with the pinned pnpm and copies
`dist/` into the API image, which serves it at `/`. The production build needs only `web/`. A
mock build reads `spec/`, `harnesswalk/`, `config/` and `datasets/_starter/`, so the e2e suites
run on the checkout, never in the image.

## Contract notes

- The UI sends `step`, `provider`, `fixture` and `disable` at the top level of `POST /v1/turns`,
  and always sends a `session_id` (a fresh one after New session), so the API's session cookie
  never decides which session a turn joins.
- Every POST, cancel included, is sent as `application/json`.
- The UI reads node ids only from `GET /v1/diagram`. The exceptions are the node whose store
  badges stand in for its children (`BADGE_NODE`, memory) and the plane colours.
