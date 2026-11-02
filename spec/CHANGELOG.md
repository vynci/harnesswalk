# Spec changelog

Changes to the language-agnostic spec (`spec/`): the prose, the contracts, the event schema,
the diagram and the conformance suite. The version rules are in [VERSIONING.md](VERSIONING.md);
the reference implementation and the package have their own [changelog](../CHANGELOG.md).
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Spec 0.1.0 is still the draft below until the first package release (`v0.1.0`).

### Added

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
