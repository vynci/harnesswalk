---
node: release
covers: [artifacts]
try:
  fixture: golden_returns_window
  prompt: "How long do I have to return a shipped order?"
---

# Inside release: artifacts

> Append-only versions of every prompt, skill and config file, and the hash chain over releases.

## What it does

The Expert view opens Release into one child, Artifacts: the store that every version and every
release lives in, one SQLite file at `data/releases.db`. Release (chapter 15) is the policy of
what gets served and when; Artifacts is the record that makes that policy trustworthy.

### Artifacts

An artifact is named by a kind and a name: `prompt/system_prompt`, `skill/<name>`, `tools/tools`,
`policy/policy`, `context/context` and `models/models`. Each change appends a version, numbered
from 1, holding the whole body and its SHA-256. Nothing is updated in place. A rollback appends
versions with old content, and an artifact a rollback removes gets a `deleted` version. The
working set is the latest version of each artifact.

A release is a frozen list of `(kind, name, version, hash)`. Its own hash covers that list, its
id, its note, who made it, and the hash of the release before it, so the releases form a chain
from a fixed genesis value. Verify walks the chain and also checks that each stored body still
hashes to what the release recorded, so an edited row cannot be served while the chain looks
fine. A `generation` counter goes up on every write; a running server compares it before each
turn, which is how it notices a release made by the CLI in another process.

## What breaks without it

Work on a copy of a data directory that has releases in it (the one from chapter 15, for
example). Pack `_starter`. First the healthy state:

```sh
export HARNESSWALK_DATA_DIR=/tmp/hw-release
uv run harnesswalk release list
```

The list ends with `chain ok`. Now play someone who wants the record to say the original release
promised 90 days, and edits the stored version directly:

```sh
sqlite3 /tmp/hw-release/releases.db \
  "UPDATE artifact_versions SET body = replace(body, '30 days', '90 days') WHERE kind = 'skill' AND name = 'returns' AND version = 1"
uv run harnesswalk release list
```

SQLite accepts the update (this store has no triggers; its integrity is the hash), and the list
now ends with `chain BROKEN`. `release list --json` says exactly where:
`{"ok": false, "broken_at": "r1", "artifact": "skill/returns@v1"}`. Without the hashes, the
edited body would simply be served as `r1`, and every turn stamped `r1` would look as if it had
always run on 90 days. Without the store at all there would be no versions to diff, no release to
roll back to, and no way to tie a turn's stamp to the text it ran on.

## The code walk

[`ArtifactStore.put`](../harnesswalk/ops/artifacts.py#ArtifactStore.put) appends a version: the
next number, the body, its hash, who and why. A body equal to the latest returns the existing
version instead of a new one, which is why saving without changes does nothing.

[`ArtifactStore.sync`](../harnesswalk/ops/artifacts.py#ArtifactStore.sync) runs at start-up and
compares each source file with what the store last saw from it; a file changed on disk becomes a
new version with `updated_by: disk` and a note naming the file. That is the bridge between editing in your editor
and editing in the web app.

[`ArtifactStore.create_release`](../harnesswalk/ops/artifacts.py#ArtifactStore.create_release)
freezes the working set, and [`release_hash`](../harnesswalk/ops/artifacts.py#release_hash) is the
chain: SHA-256 over a canonical JSON of the release's fields, the previous hash among them.
[`canonical`](../harnesswalk/ops/artifacts.py#canonical) sorts keys so the same release always
hashes the same.

[`ArtifactStore.verify`](../harnesswalk/ops/artifacts.py#ArtifactStore.verify) recomputes every
release hash oldest first, then checks every referenced version's stored hash and body. Notice the
comment about edited rows: checking the chain alone would not be enough.

[`ArtifactStore.activate`](../harnesswalk/ops/artifacts.py#ArtifactStore.activate) and
[`ArtifactStore.activations`](../harnesswalk/ops/artifacts.py#ArtifactStore.activations) keep the
log of which release was active when, including the release a rollback replaced.
[`unified_diff`](../harnesswalk/ops/artifacts.py#unified_diff) produces the diffs the UI shows on
every edit and release.

## Try this

1. **Read a history.** After an edit or two, call `GET /v1/artifacts/skill/returns`. Expected:
   the current `body`, `latest_version`, and a `history` list with `version`, `hash`,
   `updated_at`, `updated_by` and `note` for every version, rollbacks included.
2. **Edit on disk.** Change one word in `datasets/_starter/skills/order_status.md`, then run
   `uv run harnesswalk release list`. Expected: "working set dirty (unreleased changes)", because
   the start-up sync recorded your file change as a new version. Revert the file and list again:
   one more version, with the released content, so the working set is clean again.
3. **Hash it yourself.** Fetch one version's body with `GET /v1/artifacts/skill/returns?version=1`,
   save the body to a file and run `shasum -a 256` on it. Expected: the same hash as the history
   entry for version 1 (the body as stored, byte for byte).

## Check yourself

### 1. The artifact store has no triggers. What makes a direct edit visible?

- [ ] SQLite's journal.
- [x] Verify recomputes the release chain and each stored body's hash.
- [ ] The working set refuses to load it.

Answer: integrity comes from hashes, not from storage rules, so `release list` reports
`chain BROKEN` and names the artifact.

### 2. What does a release record about each artifact?

- [x] Its kind, name, version and hash.
- [ ] A full copy of the body.
- [ ] Only the artifact names.

Answer: bodies live once, in versions; a release refers to them by version and hash, and verify
checks that the two still agree.

### 3. How does a running server learn about a release made with the CLI?

- [ ] The CLI sends it an HTTP request.
- [ ] It reloads every minute.
- [x] The store's generation counter changes, and the server checks it before its next turn.

Answer: every write bumps `generation`; the server sees the change, reloads what it serves, and
the ops stream tells open browsers.

## Further reading

- [Merkle, A Digital Signature Based on a Conventional Encryption Function (CRYPTO 1987)](https://link.springer.com/chapter/10.1007/3-540-48184-2_32), the idea behind hash-linked records
- [Git documentation, Git Objects](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects), content-addressed versions in practice
- [`spec/contracts/release.md`](../spec/contracts/release.md)
