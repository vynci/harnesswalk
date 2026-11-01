# harnesswalk developer targets. Every target goes through uv.
.PHONY: setup test conformance eval run turn lint format up down web-build web-dev web-mock web-test web-e2e-mock e2e screenshots

setup: ## Create .venv and install the package with dev tools
	uv sync

test: ## Unit tests, spec checks and the conformance suite (fake provider, offline)
	uv run pytest

conformance: ## Run every spec/conformance case against the live harness, then the spec checks
	uv run python -m tests.conformance.runner
	uv run pytest tests/spec -q

eval: ## The starter golden set on the fake provider, scored and diffed against the last run: make eval PACK=_starter
	uv run harnesswalk eval $(or $(PACK),_starter) --suite golden

# The web app `make run` serves. A HARNESSWALK_WEB_DIST already set in the environment wins.
HARNESSWALK_WEB_DIST ?= $(CURDIR)/web/dist

run: ## Start the API (and web/dist when built) on 127.0.0.1:8000. Without Ollama: make run PROVIDER=fake FIXTURE=plain_answer
	@if [ -f "$(HARNESSWALK_WEB_DIST)/index.html" ]; then echo "serving the web app from $(HARNESSWALK_WEB_DIST) at http://127.0.0.1:8000/"; \
	else echo "no web app in $(HARNESSWALK_WEB_DIST) yet (make web-build); serving the API only"; fi
	HARNESSWALK_WEB_DIST="$(HARNESSWALK_WEB_DIST)" uv run harnesswalk serve $(if $(PROVIDER),--provider $(PROVIDER)) $(if $(FIXTURE),--fixture $(FIXTURE))

# pnpm installs web/ once per lock file change; every web target depends on it.
WEB_DEPS := web/node_modules/.modules.yaml
$(WEB_DEPS): web/package.json web/pnpm-lock.yaml
	pnpm --dir web install --frozen-lockfile
	@touch $@

web-build: $(WEB_DEPS) ## Build the web app into web/dist with pnpm (make run then serves it at /)
	pnpm --dir web build

web-dev: $(WEB_DEPS) ## Vite dev server on 127.0.0.1:5173, proxying /v1 to the API of make run (HARNESSWALK_API_URL)
	@echo "Vite proxies /v1 and /healthz to $${HARNESSWALK_API_URL:-http://127.0.0.1:8000}; start the API with make run"
	pnpm --dir web dev

web-mock: $(WEB_DEPS) ## The UI alone on 127.0.0.1:5173, playing recorded streams from an in-browser mock
	pnpm --dir web dev:mock

web-test: $(WEB_DEPS) ## Web lint (eslint, tsc) and unit tests (Vitest)
	pnpm --dir web lint
	pnpm --dir web test

web-e2e-mock: $(WEB_DEPS) ## Playwright smoke tests against a mock build (no API)
	pnpm --dir web exec playwright install chromium
	pnpm --dir web test:e2e

# The real-API suite starts `harnesswalk serve --provider fake` itself on this port, with its
# stores in a scratch directory, and stops it when the run ends.
E2E_PORT ?= 8765

# An idle Mac goes to sleep in the middle of a live run when nobody touches it, and a sleep
# freezes the browser, the API and Playwright's clock together: on wake the step that was
# waiting has timed out. caffeinate keeps the machine awake while the suite runs (the display
# may still sleep); where there is no caffeinate the runs start as they are.
KEEP_AWAKE := $(shell command -v caffeinate >/dev/null 2>&1 && echo caffeinate -i)

e2e: $(WEB_DEPS) ## Playwright in Chromium against a real API (fake provider) on a scratch port: make e2e E2E_PORT=8765
	pnpm --dir web exec playwright install chromium
	$(KEEP_AWAKE) env HARNESSWALK_E2E_PORT=$(E2E_PORT) pnpm --dir web test:e2e:live

screenshots: $(WEB_DEPS) ## Recapture docs/screenshots/*.png from the real API, light mode
	$(KEEP_AWAKE) env HARNESSWALK_E2E_PORT=$(E2E_PORT) HARNESSWALK_SCREENSHOTS=1 pnpm --dir web test:e2e:live e2e-live/screenshots.spec.ts

turn: ## One fake-provider turn printed as a walk: make turn FIXTURE=tool_call PROMPT="Where is A-1001?"
	uv run harnesswalk turn "$(or $(PROMPT),What are your support hours?)" --provider fake --fixture $(or $(FIXTURE),plain_answer)

lint: ## Ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Apply ruff formatting and safe fixes
	uv run ruff check --fix .
	uv run ruff format .

up: ## Docker compose dev profile: api (its image builds web/ too), postgres and ollama
	docker compose -f deploy/docker-compose.yml --profile dev up --build

down: ## Stop the compose stack
	docker compose -f deploy/docker-compose.yml --profile dev down

# --- Deploy: harnesswalk.vynci.dev on AWS (deploy/README.md) ---------------------------------
# The CDK app lives in deploy/aws (pnpm, pinned aws-cdk-lib). synth and diff read certArn from
# deploy/aws/cdk.context.json (scripts/request-cert.sh writes it); CERT_ARN=... overrides it.
# pnpm runs from inside deploy/aws, so corepack uses its pinned packageManager (as for web/).
.PHONY: deploy-test deploy-synth deploy-diff deploy-web deploy-api deploy-rollback prod-up prod-down prod-smoke loadtest
DEPLOY_DEPS := deploy/aws/node_modules/.modules.yaml
$(DEPLOY_DEPS): deploy/aws/package.json deploy/aws/pnpm-lock.yaml
	cd deploy/aws && pnpm install --frozen-lockfile
	@touch $@
CDK := cd deploy/aws && pnpm exec cdk
CDK_CERT := $(if $(CERT_ARN),--context certArn=$(CERT_ARN))

deploy-test: $(DEPLOY_DEPS) ## CDK typecheck and unit tests (SG rules, OAC, behaviours, IAM scoping)
	cd deploy/aws && pnpm typecheck
	cd deploy/aws && pnpm test

deploy-synth: $(DEPLOY_DEPS) ## cdk synth the three stacks (CERT_ARN=... before the certificate exists)
	$(CDK) synth --quiet $(CDK_CERT)

deploy-diff: $(DEPLOY_DEPS) ## cdk diff the three stacks against the account (AWS_PROFILE)
	$(CDK) diff --no-change-set $(CDK_CERT) HarnesswalkEdge HarnesswalkOrigin HarnesswalkCi

deploy-web: ## Build web/ for production, sync it to the edge bucket, invalidate index.html
	deploy/aws/scripts/deploy-web.sh

deploy-api: ## Build and push the arm64 api image (tag = git sha), then roll the origin onto it
	deploy/aws/scripts/build-push.sh
	deploy/aws/scripts/deploy-origin.sh

deploy-rollback: ## Put the origin back on an earlier image: make deploy-rollback TAG=<sha>
	@test -n "$(TAG)" || { echo "usage: make deploy-rollback TAG=<sha> (recent tags: deploy/aws/scripts/rollback.sh)"; exit 2; }
	deploy/aws/scripts/rollback.sh $(TAG)

# The prod profile on this machine (deploy/docker-compose.prod-local.yml): the image built from
# this checkout, the fake provider, Caddy's internal CA on 127.0.0.1:8443, stores in a named
# volume. HARNESSWALK_SECRET and ORIGIN_VERIFY are throwaway values made with openssl the first
# time and kept, mode 0600, outside the repo; they are never printed.
PROD_LOCAL_ENV ?= $(or $(TMPDIR),/tmp)/harnesswalk-prod-local.env
PROD_LOCAL := docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.prod-local.yml --env-file $(PROD_LOCAL_ENV) --profile prod

$(PROD_LOCAL_ENV):
	@umask 077; { echo "HARNESSWALK_IMAGE=harnesswalk-api:local"; \
	  echo "HARNESSWALK_SECRET=$$(openssl rand -hex 32)"; echo "ORIGIN_VERIFY=$$(openssl rand -hex 32)"; } > $@
	@echo "wrote throwaway secrets to $@"

prod-up: $(PROD_LOCAL_ENV) ## The prod profile locally (fake provider): https://origin.harnesswalk.vynci.dev:8443 via --resolve
	GIT_SHA=$$(git rev-parse --short=12 HEAD) $(PROD_LOCAL) up -d --build
	@echo "smoke it: make prod-smoke"

prod-down: ## Stop the local prod profile (add V=1 to delete its volumes too)
	$(PROD_LOCAL) down $(if $(V),--volumes)

prod-smoke: ## smoke.sh against the local prod profile, through Caddy
	@set -a; . $(PROD_LOCAL_ENV); set +a; \
	BASE_URL=https://origin.harnesswalk.vynci.dev:8443 SMOKE_INSECURE=1 \
	SMOKE_RESOLVE=origin.harnesswalk.vynci.dev:8443:127.0.0.1 deploy/aws/scripts/smoke.sh

loadtest: ## k6 against the local prod profile: rate limit, 50 SSE viewers, sweeper, p95 and RSS (make prod-up first)
	@command -v k6 >/dev/null || { echo "k6 is missing: brew install k6"; exit 1; }
	@set -a; . $(PROD_LOCAL_ENV); set +a; PROD_LOCAL_ENV=$(PROD_LOCAL_ENV) deploy/loadtest/run.sh
