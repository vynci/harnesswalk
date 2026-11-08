# Quickstart

Every way to run Harnesswalk, with every command. The short form is in the
[README](../README.md#quickstart). For what to do once it runs, see the
[walkthrough](walkthrough.md).

## Run it in two minutes

No model key is needed. The fake provider replays scripted turns, so the whole walk runs offline.
You need [uv](https://docs.astral.sh/uv/) (Python 3.12 or newer) and Node 24 with pnpm 10
(`corepack enable` gives you the pinned pnpm), or Docker.

```sh
git clone https://github.com/vynci/harnesswalk.git && cd harnesswalk
make setup && make web-build && make run PROVIDER=fake
```

Open http://127.0.0.1:8000. Pick a fixture in the Fixture select at the top of the page, left of
the pack picker (try `trailhead_return`), and send a message. The request walks the diagram node
by node. Click any node to see its code, config and events.

`make setup` creates `.venv` with uv. `make web-build` installs the web app's packages and builds
it into `web/dist`. `make run` serves the API and the built app from one process on
`127.0.0.1:8000`, and `PROVIDER=fake` puts the fake provider behind it. One Ctrl+C stops the
server, even with browser tabs open.

## Other ways to run it

```sh
HARNESSWALK_PROVIDER=fake make up          # Docker: api + web, postgres (pgvector) and ollama
uv run harnesswalk turn "Where is order A-1001?" --provider fake --fixture tool_call   # no browser
```

`make up` builds the web app inside the api image, so it needs no Node on your machine.
[deploy/README.md](../deploy/README.md) has the compose profiles. The
[walkthrough](walkthrough.md#run-it-other-ways) covers Docker, pgvector, public mode and
OpenTelemetry.

## From a release

You can run a release without cloning. The wheel carries the config, the spec, the packs, the
lessons and the web app. Its stores go to `./harnesswalk-data` unless `HARNESSWALK_DATA_DIR`
says otherwise.

```sh
pip install harnesswalk && harnesswalk serve --provider fake                  # http://127.0.0.1:8000
docker run --rm -p 8000:8000 -e HARNESSWALK_PROVIDER=fake ghcr.io/vynci/harnesswalk
```

## Use a real model

Bring your Anthropic key, any OpenAI-compatible endpoint, or a local model through Ollama. Each
block runs after `make setup && make web-build`. The fake provider is always there to fall back
to with no key.

### Anthropic

The router picks Claude Sonnet 5.5 or Claude Haiku 5.5 per call. The Model Selector in the
message box, or `--model` on the command line, pins one.

```sh
export ANTHROPIC_API_KEY=...                 # in your shell only, never in a file
make run PROVIDER=anthropic                  # the web app on http://127.0.0.1:8000
uv run harnesswalk turn "Where is order A-1001?" --provider anthropic --model hosted-haiku
```

### An OpenAI-compatible endpoint

This covers vLLM, llama.cpp, LM Studio, or a hosted API that speaks `/chat/completions`. The base
URL includes the version prefix.

```sh
export OPENAI_BASE_URL=http://127.0.0.1:8001/v1   # your endpoint
export OPENAI_API_KEY=...                         # if it needs one
uv run harnesswalk serve --provider openai_compatible --set models.models.compat-default.name=<model>
```

### Ollama

[Ollama](https://ollama.com) runs the model on your own machine.

```sh
ollama pull llama3.1:8b                      # about 5 GB, once
make run PROVIDER=ollama                     # plain `make run` does the same while Ollama is up
uv run harnesswalk turn "What are your support hours?" --provider ollama
```

An Ollama on another machine (a GPU box on your network) needs only its URL:
`HARNESSWALK_OLLAMA_URL=http://192.168.1.20:11434` wins over `base_url` in
`config/models.yaml` (the variable is named there as `models.providers.ollama.base_url_env`).
Besides `local-8b` (llama3.1:8b) and `local-small` (llama3.2:3b), `config/models.yaml` has the
model ladder of the [scorecard](scorecard.md): `local-2b`, `local-4b` and `local-9b`
(qwen3.5:2b, 4b and 9b) and `local-e4b` (gemma4:e4b), with thinking off through the alias's
`params: { think: false }`, which the Ollama adapter sends with every call. Pick one for a turn
with `--model local-4b`, or score one on a pack with
`uv run harnesswalk eval _starter --suite model --model local-4b`. The pack a small model does
best on is `helpdesk_lite`, the lowest rung's pack (one read-only lookup per question; local-4b
passed 16 of its 21 cases in the P2 run): `uv run harnesswalk eval helpdesk_lite --suite model
--model local-4b`.

### Hosted providers and the tests

Hosted providers are never used unless you select one. A key that merely sits in your shell
routes nothing, because `config/models.yaml` keeps `anthropic` and `openai_compatible` off until
`--provider` (or `HARNESSWALK_PROVIDER`) picks them. The automated tests (`make test`, `make e2e`
and CI) never call a hosted model. They run on the fake provider.

## The hosted sandbox

To skip setup altogether, use the hosted sandbox at https://harnesswalk.vynci.dev. It needs
nothing installed. It runs Claude Haiku 5.5, Claude Sonnet 5.5 and the fake model, with a shared
daily budget for the Claude models. Every session is deleted after two hours. The
[walkthrough](walkthrough.md#public-mode) says how to run the same public mode locally.
