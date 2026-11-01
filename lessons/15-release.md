---
node: release
try:
  fixture: golden_returns_window
  prompt: "How long do I have to return a shipped order?"
---

# Release

> Prompts, skills, policy and config as versioned artifacts with an eval gate and rollback.

## What it does

The behaviour of an agent lives mostly outside its code: in the system prompt, the skills, the
policy rules, the context budgets, the model routing and the tool definitions. harnesswalk treats
each of those files as an artifact with a history. An edit (from the web app's editors or
`PUT /v1/artifacts/{kind}/{name}`) never overwrites anything; it adds version n+1 with a diff
against n. The latest version of every artifact is the working set. A release freezes the working
set: it records which version of each artifact it contains, a hash over them, and the hash of the
release before it, so the releases form a chain you can verify.

Every file the harness loads goes through one door, `release.current()`, which answers from the
served set: the working set in dev (so an edit applies to the very next turn), the active release
elsewhere. Every turn stamps what it ran on in `turn_start.summary.release` (`release_id`,
`working_set`, `artifacts_hash`). A rollback does not rewrite history either: it adds new versions
with the old content and makes the old release active again. Some changes are refused from the
web on purpose: adding a tool entrypoint (that is new Python, so it is a file change and a
restart) and changing where provider requests and keys go (that is deployment).

## What breaks without it

Pack `_starter`, fixture `golden_returns_window`, prompt:

```text
How long do I have to return a shipped order?
```

Work on a scratch data directory so nothing of yours changes:

```sh
export HARNESSWALK_DATA_DIR=/tmp/hw-release
uv run harnesswalk turn "How long do I have to return a shipped order?" --provider fake --fixture golden_returns_window
```

The reply says 30 days, exit `answered`, and `release list` shows `* r1 ... seed ... initial`.
Now ship a bad change the way a hurried person would: change "within 30 days of delivery" to
"within 14 days of delivery" in `datasets/_starter/skills/returns.md` and run
`uv run harnesswalk release create --note "returns: 14 days"`. It prints `created r2` and
`skill/returns 1 -> 2`; `release diff r2` shows the one-line change. The same turn now ends
with exit `error`, because the fixture expects the 30 day window (on a real model the customer
would be told 14 days). Put the file back (`git checkout datasets/_starter/skills/returns.md`).

Recovery is one command: `uv run harnesswalk release rollback r1`. It prints
`rolled back to r1 (was r2)`, the list shows `* r1` and `chain ok`, and the next turn's events
start with `"release": {"release_id": "r1", ...}` and answer 30 days again. Without releases,
"which prompt was live on Tuesday?" has no answer, a bad skill has no way back except someone's
memory of the old text, and no turn records what it ran on.

The served set matters too. With the file still edited, run the turn with
`--set release.use_working_set=false`: it runs on the active release
(`working_set: false`) and answers 30 days, whatever the files say. That is how a deployment can
let people edit freely while serving only what was released.

## The code walk

[`current`](../harnesswalk/ops/release.py#current) is the door. The config loader and the
procedural store read every artifact through it; with a `Served` view it answers from the served
set, without one it reads the file on disk, which is what the read-only CLI commands do.

[`ReleaseManager`](../harnesswalk/ops/release.py#ReleaseManager) seeds the artifact store from
the files at start-up (a file changed on disk becomes a new working-set version), and answers the
edit, create, rollback and diff operations. [`ReleaseManager.stamp`](../harnesswalk/ops/release.py#ReleaseManager.stamp)
is what every `turn_start` records.

[`ReleaseManager.rollback`](../harnesswalk/ops/release.py#ReleaseManager.rollback) shows the
append-only rule in code: for each artifact whose content differs from the target release, it
`put`s the old body as a new version with the note `rollback to r1`, marks artifacts the release
did not have as deleted (also a new version), and only then activates the old release.

[`check_against_current`](../harnesswalk/ops/release.py#check_against_current) is the line
between configuration and code. A tools edit may keep or drop entrypoints but not add one; a
models edit may not change `providers`. [`validate_body`](../harnesswalk/ops/release.py#validate_body)
parses YAML and checks policy rules against a schema, and the API also dry-runs the harness on
the candidate before it accepts an edit.

[`apply_edit`](../harnesswalk/ops/release.py#apply_edit) and
[`apply_rollback`](../harnesswalk/ops/release.py#apply_rollback) are what the HTTP routes call:
they refuse while a turn or an eval run is in progress (`409 turn_running`), apply the change,
rebuild the components that read artifacts, and emit `artifact_changed` or `artifact_released`
on the tenant's ops stream, which is how a release made in the CLI shows up live in an open tab.

## Try this

1. **Try to add code from the web.** Append a tool with `entrypoint: os:system` to the tools
   artifact with `PUT /v1/artifacts/tools/tools`. Expected: `422 invalid_artifact`, "a new
   entrypoint (os:system) would run Python; add it to the pack's tools.yaml on disk and restart
   instead".
2. **Release from the browser.** Edit the `returns` skill in the Procedural inspector, then open
   Release and create a release. Expected: the status bar goes from "working set (unreleased
   changes)" to `release r2`, the release opens to a commit line "Skill returns v1 → v2" with
   the diff, and a "Release r2 active" marker lands in the chat.
3. **Watch a release arrive.** Keep the web app open and run `uv run harnesswalk release rollback r1`
   in a terminal on the same data directory. Expected: a "Rolled back" marker appears in the chat
   without a reload, and the next turn's `turn_start` names `r1`.

## Check yourself

### 1. What does a rollback do to the history of an artifact?

- [ ] It deletes the versions after the target release.
- [x] It adds new versions with the old content and makes the old release active.
- [ ] It moves a pointer back and leaves the store as it was.

Answer: history is append-only. The rollback is itself recorded, so you can see that it happened
and undo it the same way.

### 2. Why can a web edit not add a tool entrypoint?

- [ ] Because YAML cannot hold Python names.
- [ ] Because tools are frozen once released.
- [x] Because an entrypoint imports and runs Python, which is a code change, not configuration.

Answer: `check_against_current` lets an edit change what is configured, never what code runs or
where keys are sent.

### 3. How do you know which version of the prompts a past turn ran on?

- [x] Its `turn_start` event records the release id, whether the working set was served, and the artifacts hash.
- [ ] From the latest release at the time you look.
- [ ] From the git log of the repository.

Answer: every turn stamps what it was served, so a replay or an eval result can always be tied
to the exact artifacts behind it.

## Further reading

- [Sculley et al., Hidden Technical Debt in Machine Learning Systems (NeurIPS 2015)](https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html), on configuration as the largest part of an ML system
- [The Twelve-Factor App, III. Config and V. Build, release, run](https://12factor.net/build-release-run)
- [`spec/contracts/release.md`](../spec/contracts/release.md)
