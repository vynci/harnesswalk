# harnesswalk developer targets. Every target goes through uv.
.PHONY: setup test conformance run turn lint format up down web-build

setup: ## Create .venv and install the package with dev tools
	uv sync

test: ## Unit tests, spec checks and the conformance suite (fake provider, offline)
	uv run pytest

conformance: ## Run every spec/conformance case against the live harness, then the spec checks
	uv run python -m tests.conformance.runner
	uv run pytest tests/spec -q

# The web app `make run` serves. A HARNESSWALK_WEB_DIST already set in the environment wins.
HARNESSWALK_WEB_DIST ?= $(CURDIR)/web/dist

run: ## Start the API (and web/dist when built) on 127.0.0.1:8000. Without Ollama: make run PROVIDER=fake FIXTURE=plain_answer
	@if [ -f "$(HARNESSWALK_WEB_DIST)/index.html" ]; then echo "serving the web app from $(HARNESSWALK_WEB_DIST) at http://127.0.0.1:8000/"; \
	else echo "no web app in $(HARNESSWALK_WEB_DIST) yet (make web-build); serving the API only"; fi
	HARNESSWALK_WEB_DIST="$(HARNESSWALK_WEB_DIST)" uv run harnesswalk serve $(if $(PROVIDER),--provider $(PROVIDER)) $(if $(FIXTURE),--fixture $(FIXTURE))

web-build: ## Build the web app into web/dist with pnpm (make run then serves it)
	@if [ -f web/package.json ]; then \
		pnpm --dir web install --frozen-lockfile && pnpm --dir web build; \
	else \
		echo "web/ has no package.json yet, so there is nothing to build. The web app lands in P2."; \
	fi

turn: ## One fake-provider turn printed as a walk: make turn FIXTURE=tool_call PROMPT="Where is A-1001?"
	uv run harnesswalk turn "$(or $(PROMPT),What are your support hours?)" --provider fake --fixture $(or $(FIXTURE),plain_answer)

lint: ## Ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Apply ruff formatting and safe fixes
	uv run ruff check --fix .
	uv run ruff format .

up: ## Docker compose dev profile: api, postgres and ollama
	docker compose -f deploy/docker-compose.yml --profile dev up --build

down: ## Stop the compose stack
	docker compose -f deploy/docker-compose.yml --profile dev down
