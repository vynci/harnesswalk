# Generate your own harness

Harnesswalk has three verbs: read a harness (the web app and the Guide), grow one (packs and
profiles), and generate your own. `harnesswalk init` writes a repository you own: plain Python,
one file per node, with your pack, its golden set, an eval gate, the conformance suite and,
with a local model, a compose file that serves it. The output has no runtime dependency on
Harnesswalk. Its proof is the spec: a generated repo runs the same conformance cases the
reference passes, under its own profile, and the Harnesswalk web app draws its walk.

```sh
uv run harnesswalk init --profile minimal --pack helpdesk_lite --out ../my-helpdesk
cd ../my-helpdesk && make setup && make eval && make conformance && make serve
```

This first template set covers the `minimal` rung (the gateway, the context builder with the
system prompt and the session, the loop, the model layer, the tools and the trace); the
controls, the retrieving stores, the memory writer and the release chain follow as the
`governed` set.

## The inputs

A generated repo is a function of three inputs, written to `harness.yaml` in the output so an
upgrade renders the same shape again:

- **A profile**: the node set (`spec/harness.md` section 10). A profile that switches on a node
  no template implements yet is refused with the node's name (`templates/python/manifest.yaml`,
  `not_generated`).
- **A pack**: the content (`spec/packs.md`), a pack id under `datasets/` or a path to your own
  pack directory. Without `--profile` the pack's card decides.
- **A generator manifest**: what a profile does not say.

| Flag | Values | Default | Notes |
|---|---|---|---|
| `--provider` | `ollama`, `fake` | `ollama` | The real model next to the always-present fake provider |
| `--model` | an Ollama model | `qwen3.5:4b` | |
| `--transport` | `http`, `cli` | `http` | `cli` drops the HTTP API, so no web app and no conformance run |
| `--storage` | `sqlite`, `files` | `sqlite` | `files` keeps JSON Lines per table under `data/` |
| `--release` | `file_door` | `file_door` | Your files, versioned by git, loaded through one door (`chain`, the hash-chained release store, comes with the governed set) |

Modes are not a flag: only `dev` is generated. Public and private mode (anonymous tenants,
rate limits, a hosted budget, bring-your-own keys, the TTL sweeper) stay in the reference.

## What you get

```
harness/core/       managed: one file per node, the gateway walk, the stores, the HTTP API
prompts/ skills/ policy/ tools/   yours: the pack's system prompt, skills, policy rules, tools
packs/<pack>/       yours: documents, seeds, golden set and fake fixtures
config/             yours: harness.yaml (models, router, budgets, guardrails) and the profiles
evals/              yours: the golden runner, and how CI gates on it
tests/              the vendored conformance suite and runner (managed), golden and lock tests
artifacts/          the Modelfile for the served model, and release.json (the artifact hashes)
compose.yaml        harness + Ollama + the served model (with --provider ollama)
harness.yaml        the generator manifest
harness.lock        template set and spec version, and the hash of every generated file
.harnesswalk/pristine/   the managed files as generated (the base of a later three-way upgrade)
GENERATION_NOTES.md      the HTTP routes served, and everything that could not be mapped
README.md           the walkthrough for that repo: its nodes, why each exists, when to remove it
```

The runtime needs Python 3.12 and `pyyaml`; the HTTP server, SQLite, the Ollama and Anthropic
clients and the SSE stream are the standard library. The tests add `pytest`, `httpx` and
`jsonschema`. Size, measured: a `minimal` repo is about 2,300 lines of Python in `harness/`
(2,800 with tests and evals), against about 24,000 in the reference package.

## How the code is produced

Template first (option B of the agent-builder design). The templates under
`harnesswalk/gen/templates/python/` are a second, deliberately small implementation of the
spec, one file per node, with the node's contract and its lesson as the docstring. A template
is plain Python with line directives (`#% if has("policy")`) and `@@name@@` values
(`harnesswalk/gen/render.py`); `templates/python/manifest.yaml` lists every output file, the
condition it is emitted under, and the node ids it implements.

**Seams, not deletions.** A node the profile switches off is not deleted from a copy of the
reference: the walk still reaches it, and one helper, `off_node(turn, node_id, **summary)`,
opens its span and exits `skipped` with `disabled: true`. So the stream has the same shape on
every rung, the conformance paths match, and the diagram dims the node instead of losing it.
The three places the reference couples nodes (tools calling policy, approval and audit; the
gateway reaching into the memory writer; the context builder into the stores) are seam
functions in the templates, each importing its node only where it calls it.

**Other languages.** A TypeScript set would be `templates/typescript/manifest.yaml` beside
the Python one; the drift tests apply to every set. For a language without a template set, and
for pack-specific tools, the path is a coding agent working from the spec (option C): the
contracts and the conformance suite are the prompt, and `make conformance` is the acceptance
test. It is documented, not built: never ship that output without the suite passing.

## Proving it

Every generated repo carries the gate it was generated with:

```sh
make eval           # the pack's golden set on the fake provider
make conformance    # the conformance suite under the repo's profile, fake provider
make test           # both, plus a check that no file imports Harnesswalk
```

The vendored runner (`tests/conformance/runner.py`) follows the suite's protocol over HTTP: it
starts `python -m harness serve` per case, drives turns, approvals, step mode, cancel and a
restart, and asserts the stream. A case is skipped, never failed, when the profile switches off
a node it requires, when it runs in public mode (never generated), or when it needs the release
chain on a file-door repo, or when it drives the tuning HTTP API (`/v1/tune`), which is not
generated. This repository's own runner gives the same verdict through its
`--command` mode (`tests/unit/test_gen_init.py` runs both):

| Output | Profile | Conformance (fake) | Golden (fake) |
|---|---|---|---|
| `helpdesk_lite` | `minimal` | 2 of 2 (30 skipped: 26 need nodes `minimal` switches off, 4 public mode) | 21 of 21 |

Against a real model, `make conformance-model` (or `--provider ollama` with
`HARNESSWALK_OLLAMA_URL`) runs the same suite on the served model. The `cost` assertions are
dropped (the fixtures script token counts), and a case whose fixture scripts tool calls is
marked `scripted`: its walk depends on the model choosing that tool, so a failure there is the
model's choice, not a broken harness.

## Licence

The templates are Apache-2.0, like the rest of Harnesswalk. The generated code carries no
obligation: it is yours under any licence you choose. Pack content stays CC0-1.0.
