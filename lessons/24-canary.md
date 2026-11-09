---
node: release
covers: [tune, router]
try:
  pack: _starter
  fixture: tool_call
  prompt: "Where is order A-1001?"
also:
  - pack: ecommerce
    fixture: order_lookup_qf20417
    prompt: "Where is my order QF-20417?"
    note: "After tune promote, the Router's span names rule canary with its arm and bucket."
---

# Canary and rollback

> A passed gate earns a share of real turns, not all of them: the router splits, live signals decide, rollback closes it.

## What it does

The gate (chapter 23) measured a tuned model on golden cases. Real traffic is wider than any
golden set, so promotion goes in two steps through the router and the release plane, and either
step can be undone. Nothing else changes what is served.

### Tune

**Promote** (`stage: canary`) needs the model artifact's latest version to carry a passed gate,
else `409 gate_failed` with the gate in the body. It edits `models/models` in the working set:
it adds the tuned alias and sets `router.canary`, a rule with `split` instead of `use`:

```yaml
router:
  canary: { when: { pack: [ecommerce, logistics] },
            split: { base_alias: fake-student, candidate_alias: tuned-approvals-fake, fraction: 0.5 } }
```

Then each pack is released, so the change is in the release chain like any edit. **Promote
full** sets the fraction to 1, but only when the canary gate passes, else `409 canary_failed`
with both arms in the body. **Rollback** sets the fraction to 0 and releases the packs again; the
alias stays in `models/models` as the record.

**The canary gate** reads the live window since the rule was last set, per arm: turns, failed
turns (an error, a turn that hit a limit, or a thumbs down). It passes when the candidate has at
least `tune.canary.min_turns` (5) turns and its failure rate is at most
`tune.canary.max_failure_increase` (0.1) above the base's.

### Router

The router evaluates `router.canary` right after the `public` rule. A turn's **bucket** is the
first 8 hex digits of sha256 of its turn id over 2^32, so every call of one turn lands on the same
arm and the split can be checked from the trace. The arm is `candidate` when the bucket is below
the fraction. The other arm is the fallback, so a failing candidate still answers. Eval case
turns and a pinned router skip the split: a gate measures the served set, never a share of it.
The router span's summary gains `arm` and `bucket`.

## What breaks without it

Continue in the data directory of chapters 22 and 23, where `model/approvals_fake` passed its
gate. Start the API in one terminal (`HARNESSWALK_DATA_DIR=/tmp/hw-tune make run PROVIDER=fake`),
then in another:

```sh
export HARNESSWALK_DATA_DIR=/tmp/hw-tune
uv run harnesswalk tune promote --candidate model/approvals_fake --fraction 0.5
for n in 1 2 3 4 5 6 7 8 9 10 11 12; do
  uv run harnesswalk turn --provider fake --set dataset=ecommerce --fixture order_lookup_qf20417 \
    --json "Where is my order QF-20417?" 2>/dev/null \
    | jq -r 'select(.type == "node_exit" and .node_id == "router") | "\(.turn_id) \(.summary.arm) \(.summary.bucket)"' | head -1
done | tee /tmp/hw-tune/arms.txt
```

Each line is a turn, its arm and its bucket: about half are `candidate` (buckets under 0.5).
Now play a reviewer who finds the tuned model's replies wrong, and thumb those turns down:

```sh
grep candidate /tmp/hw-tune/arms.txt | while read turn arm bucket; do
  curl -s -X POST localhost:8000/v1/turns/$turn/feedback \
    -H 'Content-Type: application/json' -d '{"rating": "bad", "note": "wrong carrier"}' > /dev/null
done
curl -s localhost:8000/v1/tune/canary | jq '{arms, gate}'
uv run harnesswalk tune promote --candidate model/approvals_fake --full
uv run harnesswalk tune rollback
```

In our run 6 of 12 turns went to the candidate. The canary answers `failure_rate` 1.0 for the
candidate against 0.0 for the base and `"reason": "candidate_worse: failure rate 1.0 against 0"`
(with fewer than 5 candidate turns it says `too_few_turns`: run a few more). Promote full is
refused, `canary_failed`, exit 2. Rollback prints `"fraction": 0` and the two releases, and the
next turn's router span reads `rule=canary model=fake-student`. The web app's Tune tab shows the
same arms and history under Canary, with the Promote, Promote full and Rollback buttons.

Without the split, promote would have been all or nothing: every customer on a model only
golden cases had seen. Without the gate, `--full` would have gone through on a candidate whose
every turn was thumbed down.

The public site never promotes: its Tune tab replays exactly this sequence, recorded by
`tools/record_tune_replay.py` (the `story` in `config/fixtures/tune_replay.json`), with every
action off.

## The code walk

[`bucket_of`](../harnesswalk/execution/router.py#bucket_of) is the hash, and
[`Router._route_split`](../harnesswalk/execution/router.py#Router._route_split) picks the arm and
its fallback. [`with_canary`](../harnesswalk/execution/router.py#with_canary) puts the rule after
`public`. On the config side, the edit promote makes is
[`with_canary`](../harnesswalk/ops/tune/models_edit.py#with_canary) in `models_edit.py`.

[`Tuning.promote`](../harnesswalk/ops/tune/__init__.py#Tuning.promote) checks the gate or the
canary, edits and releases; [`Tuning.rollback`](../harnesswalk/ops/tune/__init__.py#Tuning.rollback)
sets the fraction to 0. [`Tuning._on_turn_event`](../harnesswalk/ops/tune/__init__.py#Tuning._on_turn_event)
records each split turn's arm and outcome from the event stream,
[`Tuning.canary`](../harnesswalk/ops/tune/__init__.py#Tuning.canary) counts both arms, and
[`canary_gate`](../harnesswalk/ops/tune/__init__.py#canary_gate) is the rule.

## Try this

1. **Check a bucket.** Take a turn id from `arms.txt` and run
   `python3 -c "import hashlib,sys; print(int(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:8], 16) / 2**32)" <turn_id>`.
   Expected: the bucket on that line (to 4 digits).
2. **Pass the canary.** Promote at 0.5 again and run 12 more turns without any feedback. Expected:
   the canary gate passes once the candidate has 5 turns, and `--full` sets the fraction to 1.
3. **Roll back the release instead.** `uv run harnesswalk release list --pack ecommerce` lists
   the promote and rollback releases above the first one (`initial`). Roll back to it with
   `uv run harnesswalk release rollback <id> --pack ecommerce`. Expected: `models/models` gets a
   new version with that release's content, `GET /v1/tune/canary` shows `"rule": null`, and the
   next ecommerce turn routes by `rule=default`.

## Check yourself

### 1. Why is the arm chosen by a hash of the turn id, not by a random draw per call?

- [x] Every call of a turn lands on the same arm, and the split can be checked from the trace.
- [ ] A hash is faster.
- [ ] Random numbers are not allowed in Python.

Answer: a turn's summarize and classify calls stay on its arm, and anyone can recompute the bucket.

### 2. When does Promote full go through?

- [ ] Whenever the model's gate passed.
- [x] When the candidate has enough canary turns and fails no more often than the base, within the margin.
- [ ] After 24 hours.

Answer: the golden gate earns the canary; the live canary gate earns the rest of the traffic.

### 3. What does rollback leave in `models/models`?

- [ ] Nothing: the alias is removed.
- [ ] The candidate at full share.
- [x] The alias and the rule, with the fraction at 0.

Answer: no turn reaches the candidate, and the history of the change stays in the artifact's versions.

## Further reading

- [Google SRE Workbook, Canarying Releases](https://sre.google/workbook/canarying-releases/)
- [`spec/contracts/router.md`](../spec/contracts/router.md), "0.2.0: the canary rule"
- [`spec/contracts/tuning.md`](../spec/contracts/tuning.md)
