---
node: gateway
covers: [gateway]
try:
  pack: _starter
  fixture: cancel_order
  prompt: "Please cancel order A-1002."
---

# Generate your harness

> The third verb: from a profile and a pack to a repository you own, proven by the same conformance suite.

## What it does

You have read the harness node by node and grown it rung by rung, from `minimal` to `governed`.
The last step is to own one. `harnesswalk init` takes three inputs and writes a repository:

- a **profile**, the node set: which nodes exist and which are visited as skipped spans;
- a **pack**, the content: the system prompt, skills, tools, policy, documents and golden set;
- a **generator manifest**, what a profile does not say: the real model next to the fake one,
  HTTP or a command line, SQLite or JSON Lines files, and the release door (`file_door`, your
  files under git, or `chain`, the hash-chained release store in miniature).

The output is plain Python with no runtime dependency on Harnesswalk: one file per node in
`harness/core/`, each with its contract and its lesson as the docstring, your pack's artifacts in
folders you own, the golden set wired into CI, the conformance suite vendored with its runner,
and, for a local model, a compose file that serves it. A `minimal` repo is about 2,300 lines of
Python, a `governed` one about 3,400. The web app you are reading draws its walk, because the
generated harness speaks the same event stream and the same HTTP contract.

Generation runs on your machine (`uv run harnesswalk init ...`), never in the browser: on the
public site this chapter is the walk to replay and the commands to copy.

## What breaks without it

Without a generator the move from learning to owning is a copy-and-prune of 24,000 lines, and
two things go wrong. First, deletions: cut the policy module out of a copy and every call site
that reached it breaks, or worse, keeps working with the control silently gone, and the diagram
no longer matches the stream. Second, drift: a copy has no record of where it came from, so a
later fix in the reference cannot be merged, only re-applied by hand. The surveys of harness
repositories agree on the result: most have neither a test nor an eval, and most written
security rules have no control behind them.

The generator answers both. A switched-off node is a seam, not a deletion: the walk still
reaches it and one helper emits its skipped span, so the stream keeps its shape and the
conformance paths still match. And every managed file has a pristine copy and a hash in
`harness.lock`, so `harnesswalk upgrade` can three-way merge a newer template set into your
edited files on a branch, instead of asking you to diff by eye.

## The code walk

[`plan`](../harnesswalk/gen/build.py#plan) resolves the profile and the pack, checks the
manifest, renders every template the manifest emits, copies the pack in and writes the notes,
the lock and the pristine copies. [`render`](../harnesswalk/gen/render.py#render) is the whole
template language: `#% if has("policy")` lines keep or drop the lines between them, and
`@@name@@` takes a value. In the templates, the seam is
[`off_node`](../harnesswalk/gen/templates/python/files/harness/core/events.py#off_node): a node
this turn does not run opens its span where it would run and exits `skipped` with
`disabled: true`, and the tools node reaches policy, approval and audit only through
[`decide`, `ask` and `record`](../harnesswalk/gen/templates/python/files/harness/core/tools.py#decide).

[`GenManifest.check`](../harnesswalk/gen/manifest.py#GenManifest.check) refuses what cannot be
generated rather than emitting something half-working: the `operated` rung (no templates for
online eval, diagnose and the release gate yet), public mode, and the release chain below
`governed`. Anything else the templates cannot carry, such as the tuning loop's compare and
canary steps (`--tune` generates curate and a trainer hand-off only), is listed in the output's
`GENERATION_NOTES.md`.

[`upgrade`](../harnesswalk/gen/upgrade.py#upgrade) renders the same manifest with a newer
template set, merges each managed file against its pristine copy and your committed version,
runs the codemods keyed to the lock's version, and commits all of it on a new branch in a
temporary worktree. Your working tree is never touched, and folders you own are never read.

## Try this

1. **Generate the two ends of the ladder.** `uv run harnesswalk init --profile minimal --pack
   helpdesk_lite --out /tmp/hd` and the same with `--profile governed --pack device_fleet --out
   /tmp/fleet`. Expected: `harness/core/policy.py` exists only in the second; the first one's
   GENERATION_NOTES.md lists policy among the nodes visited as skipped spans.
2. **Prove one.** In `/tmp/fleet`, `make setup && make conformance`. Expected: 21 cases pass;
   the four public-mode cases and the ones that need `diagnose`, the release gate or the tuning
   API are skipped with the reason. Then `make eval`: 28 of 28 golden cases pass.
3. **Watch it walk.** `make serve` in `/tmp/fleet`, then in this checkout
   `HARNESSWALK_API_URL=http://127.0.0.1:8000 make web-dev`. Pick `power_off_approved`, ask to
   power off KW-0002 and approve. Expected: the same walk as this harness, with Eval and Release
   dimmed.

## Check yourself

### 1. What happens to a node the profile switches off in a generated repo?

- [ ] Its file is copied and every call to it is deleted.
- [x] The walk still reaches it through a seam that emits a skipped span with `disabled: true`.
- [ ] It is hidden from the diagram the web app draws.

Answer: seams, not deletions. The stream keeps its shape on every rung, so the conformance paths
match and the diagram dims the node instead of losing it.

### 2. Which files can `harnesswalk upgrade` change?

- [ ] Every file in the repository, after asking.
- [ ] Only files you have not edited.
- [x] Only the managed files, merged on a new branch; your prompts, skills, policy, tools, packs and config are never touched.

Answer: managed files get a three-way merge against their pristine copy, with conflicts left as
markers and listed; owned folders stay yours.

### 3. How do you know a generated harness is a Harnesswalk harness?

- [ ] It imports the harnesswalk package.
- [x] It passes the conformance suite under its profile, over HTTP, like the reference.
- [ ] Its README says so.

Answer: the spec is the proof. The vendored runner and this repository's runner give the same
verdict, and neither ever imports the implementation.

## Further reading

- [Martin Fowler, Generation Gap](https://martinfowler.com/dslCatalog/generationGap.html), on keeping generated and hand-written code apart
- [`git merge-file`](https://git-scm.com/docs/git-merge-file), the three-way merge an upgrade runs
- [`docs/generator.md`](../docs/generator.md)
