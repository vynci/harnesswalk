# harnesswalk developer targets. Every target goes through uv.
.PHONY: setup test conformance run lint format

setup: ## Create .venv and install the package with dev tools
	uv sync

test: ## Unit tests plus static checks of everything under spec/
	uv run pytest

conformance: ## P0: validate cases and fixtures. P1: run the black-box suite against the harness
	uv run pytest tests/spec -q

run: ## P0: prints status. P1: starts the gateway
	uv run python -m harnesswalk

lint: ## Ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Apply ruff formatting and safe fixes
	uv run ruff check --fix .
	uv run ruff format .
