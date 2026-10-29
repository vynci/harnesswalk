# harnesswalk (reference implementation)

The Python reference implementation of the spec in `../spec/`. The distribution and the import
package are both named `harnesswalk`. Each module below registers under one node id; the same
mapping is the `source_hint` of each node in `spec/diagram/diagram.json`, and
`GET /v1/nodes/{id}` serves that file as the node's code peek. A test
(`tests/unit/test_nodes.py`) fails if a registration lives anywhere else.

Status words: **implemented** means the node does its job in P1; **stub** means it is
registered, appears in the stream with status `skipped` when it runs, and is served by
`GET /v1/diagram` with `implemented: false, stub: true`; **not registered** means the module
does not exist yet and the node is served with `implemented: false`.

| Package | Module | Node id | Contract | Status |
|---|---|---|---|---|
| `control` | `gateway.py` | `gateway` | `gateway.md`, `http_api.md` | implemented |
| `control` | `input_guardrails.py` | `input_guardrails` | `guardrail.md` | implemented (control, size; pii and injection P3) |
| `control` | `policy.py` | `policy` | `policy.md` | implemented |
| `control` | `approval.py` | `approval` | `approval_queue.md` | implemented (restart resume P3) |
| `control` | `audit.py` | `audit` | `audit.md` | implemented |
| `context` | `builder.py` | `context_builder` | `context_builder.md` | implemented |
| `context` | `stores/__init__.py` | `memory` | `store.md` | implemented |
| `context` | `stores/procedural.py` | `procedural` | `store.md` | implemented |
| `context` | `stores/session.py` | `session` | `store.md` | implemented |
| `context` | `stores/semantic.py` | `semantic` | `store.md` | implemented (P3) |
| `context` | `stores/episodic.py` | `episodic` | `store.md` | implemented (P3) |
| `context` | `stores/knowledge.py` | `knowledge` | `store.md` | implemented (P3) |
| `context` | `memory_writer.py` | `memory_writer` | `memory_writer.md` | implemented (P3) |
| `execution` | `loop.py` | `loop` | `loop.md` | implemented |
| `execution` | `model_layer.py` | `model_layer` | `model_provider.md` | implemented |
| `execution` | `router.py` | `router` | `router.md` | implemented |
| `execution` | `providers/` (`fake.py`, `ollama.py`, `anthropic.py`, `openai_compatible.py`)` | `providers` | `model_provider.md` | implemented |
| `execution` | `tools.py` | `tools` | `tool_registry.md` | implemented |
| `execution` | `registry.py` | `registry` | `tool_registry.md` | implemented |
| `execution` | `sandbox.py` | `sandbox` | `sandbox.md` | implemented (in-process level only) |
| `execution` | `output_guardrails.py` | `output_guardrails` | `guardrail.md` | implemented |
| `ops` | `trace.py` | `trace` | `tracer.md` | implemented |
| `ops` | `cost.py` | `cost` | `tracer.md` | implemented |
| `ops` | `eval/__init__.py` | `eval` | `evaluator.md` | stub (P4) |
| `ops` | `eval/online.py` | `online_eval` | `evaluator.md` | not registered (P4) |
| `ops` | `eval/offline.py` | `offline_eval` | `evaluator.md` | not registered (P4) |
| `ops` | `eval/diagnose.py` | `diagnose` | `evaluator.md` | not registered (P4) |
| `ops` | `release.py` | `release` | `release.md` | stub (`current()` is real; P4) |
| `ops` | `artifacts.py` | `artifacts` | `release.md` | not registered (P4) |

Modules that are not nodes:

| Package | Module | Purpose |
|---|---|---|
| (root) | `events.py` | The emitter: builds events, assigns `seq`, enforces nesting, validates, breakpoints; the event bus |
| (root) | `nodes.py` | The node registry and `node()`, the span every component runs inside |
| (root) | `diagram.py` | Loads `spec/diagram/diagram.json`, the only source of node and edge ids |
| (root) | `types.py` | Shared records from `spec/contracts/README.md` |
| (root) | `runtime.py` | Builds every component from the settings and wires them together |
| (root) | `inspectors.py` | What `GET /v1/nodes/{id}` shows per node kind, and the code peek of `GET /v1/nodes/{id}/source` |
| (root) | `turns.py` | Turn summaries read off the stored events: `GET /v1/turns/{id}` and `GET /v1/sessions/{id}/turns` |
| (root) | `paths.py` | Where `config/`, `spec/` and `datasets/` live (`HARNESSWALK_ROOT`) |
| (root) | `__main__.py` | The `harnesswalk` command: `serve`, `turn`, `events`, `turns`, `ingest` |
| `config` | `loader.py` | Loads `config/*.yaml` and profiles through release, applies env and overrides |
| `control` | `http_api.py` | The FastAPI app for `spec/contracts/http_api.md`, plus the built web app (`web/dist`) and dev-mode CORS |
| `context` | `tokens.py` | The `chars_div_4` token estimator |
| `context` | `records.py` | Store records (`Query`, `Hit`, `SourceRef`), the shared pack scope, tokens, entities, cosine and reciprocal rank fusion |
| `context` | `embeddings.py` | Local embeddings: hash (`fake`) and Ollama, chosen by `stores.embeddings.provider` |
| `context` | `chunking.py` | Markdown and PDF documents into chunks: by heading, then by size with overlap |
| `context` | `stores/memory_db.py` | Opens the SQLite file behind semantic, episodic, knowledge and the writer |
| `control` | `http_memory.py` | The context plane routes: memory proposals and knowledge documents |
| `execution` | `builtin_tools.py` | `calculator` and `read_skill` |
| `execution` | `providers/_http.py` | One POST with errors mapped to codes, shared by the HTTP adapters |
| `adapters` | `storage/sqlite.py` | The SQLite helper behind the trace, session, audit and approval stores |
| `adapters` | `storage/vectors.py` | Vector indexes: `sqlite` (brute-force cosine) and `pgvector`, chosen by `storage.vectors` |
| `adapters` | `mcp/` | MCP client that registers server tools (later phase) |
| `adapters` | `examples/` | How LangGraph, Strands or the Claude Agent SDK would plug in (P7, never imported by the core) |

Rule: no module in `control`, `context`, `execution` or `ops` may import an agent framework.
