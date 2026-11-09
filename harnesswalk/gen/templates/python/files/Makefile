# Targets for this harness. Python 3.12+; `make setup` installs the two runtime dependencies
# (pyyaml, plus nothing else: the providers use the standard library) and the test tools.
.PHONY: setup serve turn eval test conformance conformance-model lint
PY ?= python3
PROVIDER ?= fake

setup: ## a virtualenv with the runtime and test dependencies
	$(PY) -m venv .venv && .venv/bin/pip install -q -e ".[test]"

serve: ## the HTTP API on 127.0.0.1:8000 (PROVIDER=@@provider@@ for the real model)
	.venv/bin/python -m harness serve --provider $(PROVIDER)

turn: ## one turn, printed as a walk: make turn MSG="..." FIXTURE=<fake fixture>
	.venv/bin/python -m harness turn "$(MSG)" --provider $(PROVIDER) $(if $(FIXTURE),--fixture $(FIXTURE))

eval: ## the golden set on the fake provider
	.venv/bin/python -m evals.run_golden

conformance: ## the Harnesswalk conformance suite under this profile, fake provider
	.venv/bin/python -m tests.conformance.runner --profile @@profile@@

conformance-model: ## the same suite against a real model: HARNESSWALK_OLLAMA_URL=http://host:11434
	.venv/bin/python -m tests.conformance.runner --profile @@profile@@ --provider @@provider@@

test: ## unit checks, the golden set and the conformance suite
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check .
