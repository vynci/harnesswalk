# Spec changelog

Changes to the language-agnostic spec (`spec/`): the prose, the contracts, the event schema,
the diagram and the conformance suite. The version rules are in [VERSIONING.md](VERSIONING.md);
the reference implementation and the package have their own [changelog](../CHANGELOG.md).
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Spec **0.2.0**, the first minor release after 0.1.0. Everything that conformed to 0.1.0 still
conforms when it runs every pack on `operated`, the default profile.

### Added

- Canary routing and scripted students (P3 groundwork, additive, still 0.2.0):
  `router.canary`, a rule with `split` instead of `use` and the `when.pack` key
  (`contracts/router.md`, "0.2.0: the canary rule"); fake styles `ask_first` and `over_call`
  and `ToolSchema.action_class` (`contracts/model_provider.md`); `evals.golden_paths`, the
  `tool_choice` evidence and `levers` of a diagnosis (`contracts/evaluator.md`).
- Pack format (P2, additive, still 0.2.0; `packs.md`, `contracts/evaluator.md` "P2
  additions"): multi-turn golden cases (`turns: [{user, expect?, checks?}]` in place of
  `prompt`, played in one session, scored per turn as `turn<n>.<scorer>` and over the
  session; `fixture` may be a list aligned with the turns), a case's `roles`, the `checks`
  keys `compacted` and `summary`, the `skills/policy.md` convention with validator rule 10,
  and `provenance: {card, generator: {model, date, prompt_hash}}` on the pack card. The
  domain tier's pack-tool count widens from 2 to 4 to 2 to 12.
- Harness profiles (`harness.md` section 10): a named harness shape, a file with `id`,
  `title`, `summary`, `rung`, `disable`, `stores` and `overrides`, validated on load. Six ship as
  the ladder `minimal`, `guarded`, `grounded`, `remembered`, `governed`, `operated` (everything
  on, the 0.1.0 harness). A switched-off node is dimmed, never hidden: it still enters and exits
  its span where it would run, `skipped` with `summary.disabled: true`. Public mode never
  switches off the controls.
- `can_disable` widened from 4 to 13 nodes (`diagram/diagram.json`): `approval`, `audit`,
  `knowledge`, `semantic`, `episodic`, `eval`, `online_eval`, `diagnose` and `release` join the
  guardrails, `policy` and `memory_writer`. `harness.md` 10.3 says what "off" means for each and
  which invariants hold; 10.3 also lists the nodes that never switch off and why.
- `turn_start.summary.profile` `{id, rung, off}` (`events.md` section 6, `events.schema.json`).
- `pack.yaml` `profile`, required (`packs.md`), and validator rule 9: demonstrated nodes on,
  golden cases reachable, no tool that needs a human under a profile without approval.
- `GET /v1/profiles`, `GET /v1/diagram?pack=`, `profile` on pack items, `requires` on lessons,
  gate reason `profile` (`contracts/http_api.md`, "0.2.0 additions").
- Conformance: case keys `requires` and `profile` (`conformance/case.schema.json`), per-profile
  runs (`conformance/README.md`, Profiles), and three cases: `profile_minimal`,
  `profile_minimal_tool`, `approval_off`.
- Payloads (`contracts/tracer.md`, "Payloads"): the gateway, the input guardrails, the context
  builder, the model layer and the output guardrails record what each hop carried as a typed
  detail record on their `node_enter` and `node_exit` (texts capped at 32 000 characters with a
  `truncated` flag), and `GET /v1/trace/turns/{turn_id}/details/{event_id}` serves one to the
  turn's own tenant (`contracts/http_api.md`, Trace). Those events gain one summary key,
  `has_payload: true`; nothing else in the stream changes.
- Document files (`contracts/http_api.md`, "Context plane routes" and "Packs"): the knowledge
  document routes gain `file` (`{pack, path, url, media_type}` or `null`), and
  `GET /v1/packs/{pack_id}/files/docs/{name}?raw=1` serves a pack document's original file
  (`application/pdf` or `text/markdown; charset=utf-8`, inline, cacheable) in every mode, only
  from the pack's `docs/`.
- The `model` eval suite (`contracts/evaluator.md`, "Model suite and the scorecard";
  `contracts/http_api.md`, Evals): the cases answer on one model alias, pinned for every call
  (`contracts/router.md`, "Model suite: the pinned router"), and the deterministic scorers
  judge them, with no LLM judge. A `model` run is compared with the previous run of the same
  model. Additive: no new event type, no version change.
- Grading modes (`contracts/evaluator.md`): the `model` suite grades `tool_calls` as ordered
  (the expected calls in order, extra calls to read tools allowed; `expect.tool_calls_mode:
  exact` keeps a case strict) and `answer_match` on normalized text (case, Unicode, dates,
  times, amounts, numbers); `golden` stays exact. A `judge` run that names a model pins it, and
  a pinned alias ignores `max_price_out_per_mtok` (`contracts/router.md`).
- Structured replies (`contracts/guardrail.md`, `contracts/tracer.md`): a reply that wraps a
  schema-valid JSON value in prose or a code fence is repaired to that value (`repair`, kind
  `schema`, payload `schema_extracted: true`) instead of blocked. New conformance case
  `schema_extracted`.

### Changed

- `schema_version` is the pattern `^0\.[0-9]+\.[0-9]+$` instead of the constant `0.1.0`; the
  schema `$id` paths move to `/spec/0.2/`. `SPEC_VERSION`, `diagram.json` and the conformance
  profile say `0.2.0`; `examples/` are re-recorded at 0.2.0 with `turn_start.summary.profile`.
- `policy` MUST still send to audit what it did; with `audit` off the record is not kept.
  `approval` off turns `needs_approval` into a deny (fail closed).

## [0.1.0] - draft, frozen at the v0.1.0 release

### Added

- P0: `harness.md` (planes, 15 simple-view and 14 expert nodes, invariants, the tenant rule,
  modes, step mode, replay), `diagram/diagram.json`, `events.schema.json` and `events.md`
  (event types, statuses, stream rules, the OpenTelemetry mapping), the component contracts
  and `contracts/http_api.md`, `examples/`, and the conformance protocol, profile and first
  cases.
- P1 to P6: additions recorded in place in each document (marked "P3", "P4 additions" and so
  on): the vertical slice, the UI's endpoints, the full control and context planes, LLM Ops,
  dataset packs (`packs.md`) and public mode. 23 conformance cases.
- P7: `VERSIONING.md`, this file, and a conformance README written for implementers: what a
  conforming runtime is, the fixture protocol, and how to run the suite against any HTTP
  implementation (`--command`, one process per case with the variables it is given, and
  `--base-url`, one running server).

### Changed

- P7: conformance protocol step 1. A shared server is told each case's fixture per turn, in
  the request body (`fixture`, the dev-only option already in `contracts/http_api.md`), instead
  of the `X-Harnesswalk-Fixture` header the protocol had proposed and no implementation read.

[Unreleased]: https://github.com/vynci/harnesswalk/commits/main/spec
