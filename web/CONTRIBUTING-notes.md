# Integration notes for the P2 web app

What the integration pass needs to wire after `p2-web` merges into `main`.

## Makefile

- `make web-build` (already on main) runs `pnpm --dir web install --frozen-lockfile` and
  `pnpm --dir web build`. That works as is. `tsc -b` runs first and type checks the mock too,
  which reads `../spec`, `../harnesswalk`, `../config` and `../datasets` through `?raw` and
  `import.meta.glob`; those are untyped string imports, so the type check needs only `web/`.
- Suggested extra targets: `web-dev` (`pnpm --dir web dev`), `web-mock` (`pnpm --dir web
  dev:mock`), `web-test` (`pnpm --dir web test && pnpm --dir web lint`), `web-e2e`
  (`pnpm --dir web exec playwright install chromium && pnpm --dir web test:e2e`).
- A target for the live suite: start `harnesswalk serve --provider fake --fixture plain_answer
  --port 8765` in the background, run `HARNESSWALK_API_URL=http://127.0.0.1:8765 pnpm --dir web
  test:e2e:live`, stop the server. It passed 5/5 against main at e69629b.

## Compose and the Docker image

- The production build (`pnpm build`) needs only `web/` in the node stage. It does not import
  `spec/` or anything else outside `web/`.
- A mock build (`pnpm build:mock`, used by `test:e2e`) also needs `spec/`, `harnesswalk/`,
  `config/` and `datasets/_starter/`, so run the e2e suite in CI on the checkout, not in the image.
- `pnpm-lock.yaml` is committed; use `--frozen-lockfile`. Node 24, pnpm 10.33 (`packageManager`).

## CI

- Add a web job: `pnpm install --frozen-lockfile`, `pnpm lint`, `pnpm test`, `pnpm build`,
  `pnpm exec playwright install --with-deps chromium`, `pnpm test:e2e`.
- Optionally the live suite after `make conformance`, as above.

## README

- Screenshots for the root README: the simple view mid-turn, the approval card, the expert view
  and the inspector on Policy. Mock mode with `?mockStepMs=400` makes them easy to time.
- The root README quick start should mention `make web-build` then `make run`, and
  `pnpm --dir web dev:mock` for a UI-only look.
- `web/README.md` replaces the old placeholder.

## Contract follow-ups

- The UI sends the P2 body fields `step`, `provider`, `fixture` and `disable` at the top level of
  `POST /v1/turns` (as CONTRACT.md lists them), not inside `options`.
- `GET /v1/turns/{id}` and the session list return `message`; the UI uses it for history and
  falls back to what this browser remembers when it is absent.
