# Spec versioning

The spec is a contract between implementations (the Python reference, a port in another
language) and consumers (the web app, a trace viewer, an eval tool). This file says what its
version number promises, which changes each kind of release may contain, and how a field, an
event or a node is retired. `spec/harness.md` section 9 is the short form; this is the full rule.

## One number

`SPEC_VERSION` is a single [semantic version](https://semver.org/spec/v2.0.0.html) for
everything under `spec/`: the prose, the contracts, `events.schema.json`, `diagram/diagram.json`
and the conformance suite. It appears in:

| Where | As |
|---|---|
| `spec/harness.md`, `spec/events.md` | The version line at the top |
| `spec/events.schema.json` | `schema_version` on every event, and the `$id` path (`/spec/<major>.<minor>/`) |
| `spec/diagram/diagram.json` | `spec_version` |
| `spec/conformance/profile.yaml`, every `cases/*.yaml` | `spec_version` |
| An implementation | `GET /healthz` `spec_version`; `harnesswalk.SPEC_VERSION` in the reference |

The package version (`harnesswalk` on PyPI, the `v*` tags) is separate. Harnesswalk 0.1.0
implements spec 0.1.0, but the two move independently: a release of the reference that fixes a
bug changes only the package version, and a spec change ships in whichever package release
first implements it. `CHANGELOG.md` records the package, `spec/CHANGELOG.md` the spec.

## What each part of the version means

**Patch** (0.1.0 to 0.1.1): no observable change. Wording, examples, typos, a clarification
that every conforming implementation already satisfies, a fix to a conformance case that
asserted something the spec never required. An implementation that passed before passes after.

**Minor** (0.1 to 0.2): additive. Everything that conformed before still conforms, and a
consumer built for the older version still works on a newer stream if it follows the consumer
rules below. Allowed:

- a new optional event field, summary key or error code;
- a new event type, a new ops event, a new status in a summary (not in the top-level
  `status`), a new enum value in a summary field;
- a new expert-view node or edge, a new lesson, a change to `walk_order`;
- a new HTTP endpoint, an optional request field, a new response field;
- a new conformance case, a new key in the profile's `overridable` list;
- deprecating anything (see below).

**Major** (0.x to 1.0, 1.x to 2.0): breaking. Required for:

- removing or renaming a node id, edge id, event type, field, status, endpoint or error code;
- a new simple-view node (every case's `path` changes, and every implementation must emit it);
- a new required field, or a new value of the top-level `status`;
- changing what an existing field, status code or rule means;
- changing a conformance case so that an implementation that passed it fails.

The same rules hold while the major is 0. Pre-1.0 is not a licence to break things in a minor:
the hosted sandbox, the lessons and any port depend on the same stream.

Spec 0.1.0 was a draft until the first package release (`v0.1.0`). Phases P1 to P6 added to it
in place, each addition marked in its document ("P3", "P4 additions" and so on) and every one of
them additive. From the `v0.1.0` tag on, 0.1.0 is frozen and every change follows these rules.

## Consumer rules

A consumer (the web app, a trace store, an eval tool) built for spec `X.Y`:

- MUST reject an event whose `schema_version` has a major other than `X`;
- MUST ignore event types, summary keys, `ext` contents and endpoints it does not know, rather
  than fail (an unknown event type is not drawn; an unknown node id inside a known event is
  shown as an unknown node, never dropped silently from the trace);
- SHOULD validate strictly only what it produced or what its own version defines.

A producer (a harness) always emits the exact `schema_version` it implements, and the
conformance suite validates its events against the schema of that version.

## How `events.schema.json` changes

- The schema is JSON Schema 2020-12 and closed at the top level (`additionalProperties:
  false`), so every new top-level field is a schema change with a version bump, never an
  implementation's private addition. Implementation-specific data goes in `ext`.
- `schema_version` was `const: "0.1.0"` in 0.1.0. Since 0.2.0, the first minor release, it is a
  pattern for the whole major (`^0\.[0-9]+\.[0-9]+$`), so streams recorded by older producers
  still validate.
- A new event type is a new value in `type`'s enum plus its own `if`/`then` branch; the
  branches of existing types are not loosened or tightened in a minor.
- `required` never grows in a minor. A field that will become required is first added as
  optional, with the spec text saying so.
- The `$id` carries `<major>.<minor>`: a minor release publishes a new
  `https://harnesswalk.vynci.dev/spec/0.2/events.schema.json` and the old URL keeps serving the
  old file. `spec/examples/*.jsonl` are re-recorded at the new version.

## How `diagram.json` changes

- Node ids and edge ids are permanent within a major. They are the join key between code,
  events, the UI, the lessons and the conformance paths.
- Adding an expert node (with its parent) or an edge is minor. Adding a simple-view node is
  major. Moving a node to another group or view is major.
- Labels, lesson text, `lesson_summary`, `lesson_url`, layout hints and `source_hint` (which
  names a file of the reference implementation) are presentation: changing them is a patch,
  and other implementations may ignore them.
- `walk_order` and `paths` may change in a minor, with the lessons index in the same commit
  (a test keeps them in step).

## How the conformance suite changes

- The suite version is the spec version. Each case's `spec_version` is the version that added
  it or last changed what it asserts. An implementation of spec `X.Y` must pass every case
  whose `spec_version` is `X.Y` or lower within major `X`.
- New cases arrive in minor versions, with the spec text they test. A case is never made
  stricter in a minor; to assert more, add a case.
- Fixtures follow their cases. The fake provider's fixture format is part of the spec
  (`spec/contracts/model_provider.md`) and changes under the same rules.

## Deprecation

1. A release deprecates a field, event type, endpoint, node or case by saying so where it is
   defined ("Deprecated in 0.3; removed in 1.0. Use `x` instead.") and under **Deprecated** in
   `spec/CHANGELOG.md`.
2. A deprecated item keeps working: producers keep emitting it and the suite keeps asserting it
   for at least one minor release after the deprecation and until the next major.
3. Only a major release removes it, listed under **Removed**.
4. A replacement is added (minor) before or with the deprecation, so there is always one
   release where both work.

## Making a spec change

1. Edit the spec text, schema or diagram, and the conformance case that shows the behaviour.
2. Add a line under `Unreleased` in `spec/CHANGELOG.md` (Added, Changed, Deprecated, Removed or
   Fixed) naming the files.
3. When a spec release is cut, bump `SPEC_VERSION` everywhere in the table above in one commit
   (`harnesswalk/__init__.py`, the version lines, `diagram.json`, the profile, the schema `$id`
   and `schema_version`), move the changelog section, and re-record `spec/examples/`.
