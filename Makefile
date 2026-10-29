# harnesswalk developer targets. Every target goes through uv.
.PHONY: setup test conformance run turn lint format up down

setup: ## Create .venv and install the package with dev tools
	uv sync

test: ## Unit tests, spec checks and the conformance suite (fake provider, offline)
	uv run pytest

conformance: ## Run every spec/conformance case against the live harness, then the spec checks
	uv run python -m tests.conformance.runner
	uv run pytest tests/spec -q

run: ## Start the API on 127.0.0.1:8000. Without Ollama: make run PROVIDER=fake FIXTURE=plain_answer
	uv run harnesswalk serve $(if $(PROVIDER),--provider $(PROVIDER)) $(if $(FIXTURE),--fixture $(FIXTURE))

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
