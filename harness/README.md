# harness (reference implementation)

The Python reference implementation of the spec in `../spec/`. The distribution is named
`harnesswalk`; the import package is `harness`. P0 ships the package skeleton only. Modules
below are planned, each mapped to the node id it registers under. The same mapping is the
`source_hint` of each node in `spec/diagram/diagram.json`, and `GET /v1/nodes/{id}` serves that
file as the node's code peek.

| Package | Planned module | Node id | Contract |
|---|---|---|---|
| `control` | `gateway.py` | `gateway` | `gateway.md`, `http_api.md` |
| `control` | `input_guardrails.py` | `input_guardrails` | `guardrail.md` |
| `control` | `policy.py` | `policy` | `policy.md` |
| `control` | `approval.py` | `approval` | `approval_queue.md` |
| `control` | `audit.py` | `audit` | `audit.md` |
| `context` | `builder.py` | `context_builder` | `context_builder.md` |
| `context` | `stores/__init__.py` | `memory` | `store.md` |
| `context` | `stores/procedural.py` | `procedural` | `store.md` |
| `context` | `stores/session.py` | `session` | `store.md` |
| `context` | `stores/semantic.py` | `semantic` | `store.md` |
| `context` | `stores/episodic.py` | `episodic` | `store.md` |
| `context` | `stores/knowledge.py` | `knowledge` | `store.md` |
| `context` | `memory_writer.py` | `memory_writer` | `memory_writer.md` |
| `execution` | `loop.py` | `loop` | `loop.md` |
| `execution` | `model_layer.py` | `model_layer` | `model_provider.md` |
| `execution` | `router.py` | `router` | `router.md` |
| `execution` | `providers/` (`fake.py`, `ollama.py`, `anthropic.py`, `openai_compatible.py`) | `providers` | `model_provider.md` |
| `execution` | `tools.py` | `tools` | `tool_registry.md` |
| `execution` | `registry.py` | `registry` | `tool_registry.md` |
| `execution` | `sandbox.py` | `sandbox` | `sandbox.md` |
| `execution` | `output_guardrails.py` | `output_guardrails` | `guardrail.md` |
| `ops` | `trace.py` | `trace` | `tracer.md` |
| `ops` | `cost.py` | `cost` | `tracer.md` |
| `ops` | `eval/__init__.py` | `eval` | `evaluator.md` |
| `ops` | `eval/online.py` | `online_eval` | `evaluator.md` |
| `ops` | `eval/offline.py` | `offline_eval` | `evaluator.md` |
| `ops` | `eval/diagnose.py` | `diagnose` | `evaluator.md` |
| `ops` | `release.py` | `release` | `release.md` |
| `ops` | `artifacts.py` | `artifacts` | `release.md` |

Modules that are not nodes:

| Package | Planned module | Purpose |
|---|---|---|
| (root) | `events.py` | The emitter: builds events, assigns `seq`, enforces nesting, validates in dev |
| (root) | `nodes.py` | The node registry; fails at startup on an id that is not in the diagram |
| `config` | `loader.py` | Loads `config/*.yaml` through release, applies mode rules |
| `adapters` | `storage/sqlite.py`, `storage/postgres.py` | Storage backends behind the store, trace and audit contracts |
| `adapters` | `mcp/` | MCP client that registers server tools (later phase) |
| `adapters` | `examples/` | How LangGraph, Strands or the Claude Agent SDK would plug in (P7, never imported by the core) |

Rule: no module in `control`, `context`, `execution` or `ops` may import an agent framework.
