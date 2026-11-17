---
node: release
covers: [tune]
try:
  pack: _starter
  fixture: cancel_order
  prompt: "Please cancel order A-1002."
---

# Tune: train the model, then gate it

> A dataset in, an adapter and a served model out, gated like any release, canaried through the router.

## What it does

Curate (chapter 22) made `dataset/approvals`. Tune is an expert child of Release because what it
produces is handled like any other change: versioned, gated, shipped through a release and
rolled back the same way.

### Tune

A **trainer** sits behind one interface, like a provider, under an alias in
`config/trainers.yaml`. `fake` is deterministic and instant: a fixed loss curve, a fake weights
hash, and a model served by the fake provider. Its `outcome` is `good` (the model answers like the
fixtures) or `forgets` (it calls a tool when none is needed). `homelab-trl` is a GPU worker over
ssh: it copies the dataset in, trains a LoRA adapter with TRL, merges it, converts and quantizes
the merge to GGUF and creates the Ollama model (`deploy/homelab/README.md`). `local-mlx` is
documented and refused with `422 trainer_unavailable`.

A run streams `tune_step` events (step, loss, validation loss, time left) on `tune:<run_id>`,
which the Tune tab draws as a loss curve. On success it records an `adapter/<name>` version (the
training config, the base model and revision, the metrics, the weights by sha256) and a
`model/<name>` version (the alias it will be served under and its Modelfile), chained by hash to
the dataset. Weights never enter the store or git; only their hashes do. A run never serves
anything.

**Compare is the gate.** It runs the `model` suite on the target packs and on canary packs the
dataset never touched, for three arms on the same day and the same grading: the base, the
candidate, and the **prompt arm** (the base with the control arm's text in its system prompt).
The candidate passes when it gains at least `tune.gate.min_gain_cases` (2) over the base on the
target packs and loses at most `tune.gate.canary_tolerance_cases` (1) on each canary pack. The
verdict is appended to the model artifact as a new version, so promote can check it.

**Serve what you evaluate.** On the homelab the gate runs against the quantized GGUF that Ollama
serves, not the training checkpoint, because that is what turns will meet.

## What breaks without it

Continue in the data directory of chapter 22 (`dataset/approvals` is there). Train twice on the
fake trainer:

```sh
export HARNESSWALK_DATA_DIR=/tmp/hw-tune
uv run harnesswalk tune run --dataset approvals --trainer fake --student fake-student --name approvals_fake
uv run harnesswalk tune run --dataset approvals --trainer fake --student fake-student --config outcome=forgets --name forgets_fake
```

Each prints 18 steps (the loss falls from about 1.85 to 0.35) and an adapter and model at v1. Now the gate:

```sh
uv run harnesswalk tune compare --candidate model/approvals_fake --against fake-student \
  --packs ecommerce,logistics --canary-packs sports,_starter --samples 1
```

It passes: ecommerce 13/24 for the base against 24/24 for the candidate, logistics 20/24 against
24/24, a gain of 15 cases; sports holds at 22/22 and `_starter` goes from 15 to 16 of 16. The same
command with `--candidate model/forgets_fake --out /tmp/hw-tune/cmp.md` exits 1 and writes the
table: the candidate gains on ecommerce (18/24) but loses on logistics (16/24), and both canaries
drop past the tolerance (sports 16/22, `_starter` 11/16). The verdict is `failed`, reason
`below_min_gain` (a gain of 1), with both canary packs in `regressed`. The `tool_calls` split says
why: 14 failed cases called a tool when none was expected. Then:

```sh
uv run harnesswalk tune promote --candidate model/forgets_fake
```

`harnesswalk tune: gate_failed: model/forgets_fake has no passed gate`, exit 2. Without the gate,
a loss curve that looks just as good (both runs end at 0.35) would have shipped a model that
forgot how to answer without tools.

**Weights against words.** The prompt arm column is the cheaper lever, measured beside every
candidate. On the fake loop it gains nothing (13/24 on ecommerce, like the base) because the
scripted student ignores prompts; a real small model does read them. The measured numbers for
qwen3.5:4b on the homelab, both levers side by side, are in [docs/tuning.md](../docs/tuning.md).

## The code walk

[`FakeTrainer.train`](../harnesswalk/ops/tune/trainers.py#FakeTrainer.train) is the whole fake
trainer: steps from the dataset size, a loss curve from its hash, a model alias on the fake
provider. [`SshTrainer.train`](../harnesswalk/ops/tune/trainers.py#SshTrainer.train) is the real
one's driver: copy in, start, poll a status file, read the result back.
[`build_trainer`](../harnesswalk/ops/tune/trainers.py#build_trainer) picks one by `kind`.

[`Tuning._run`](../harnesswalk/ops/tune/__init__.py#Tuning._run) emits the step events and records
the adapter and model manifests.
[`Tuning._compare`](../harnesswalk/ops/tune/__init__.py#Tuning._compare) builds the three views
(nothing is edited) and runs the suite; [`gate_verdict`](../harnesswalk/ops/tune/__init__.py#gate_verdict)
is the rule. [`Tuning.promote`](../harnesswalk/ops/tune/__init__.py#Tuning.promote) refuses a
model whose latest version has no passed gate.

## Try this

1. **Watch the curve.** Start `make run PROVIDER=fake` with the same `HARNESSWALK_DATA_DIR` (and
   the web app built with `make web-build`), switch on the Advanced view and click Tune inside
   Release. Expected: the Tune tab, Runs
   showing both runs with their loss curves, and Compare showing both tables with the verdicts.
2. **Ask for a stub.** `uv run harnesswalk tune run --dataset approvals --trainer local-mlx
   --student fake-student`. Expected: `trainer_unavailable`, exit 2, and no run recorded.
3. **Read the chain.** `uv run harnesswalk tune status`. Expected: `verify` with `ok: true`, and
   `model/approvals_fake` at v2 (v1 from the run, v2 with the gate).

## Check yourself

### 1. Both fake runs end at the same training loss. What tells them apart?

- [ ] The validation loss.
- [x] The gate: the model suite on the target and the canary packs.
- [ ] The adapter's sha256.

Answer: the loss says how well the student fits the dataset, not what it forgot; the canary packs show that.

### 2. Why does compare run a prompt arm?

- [ ] To train the prompt.
- [ ] To replace the base.
- [x] To measure the cheaper lever on the same cases, so you only tune when words are not enough.

Answer: the prompt arm reports, it does not gate; its gain beside the candidate's is the argument for or against training.

### 3. What does a run change about what turns are served on?

- [x] Nothing: a model is served only after compare and promote.
- [ ] It adds the alias to the router.
- [ ] It releases the packs.

Answer: a run records artifacts; promote is the only step that edits `models/models`, through a release.

## Further reading

- [Hu et al., LoRA: Low-Rank Adaptation of Large Language Models (2021)](https://arxiv.org/abs/2106.09685)
- [Kirkpatrick et al., Overcoming catastrophic forgetting in neural networks (2017)](https://arxiv.org/abs/1612.00796), why the gate has canary packs
- [`spec/contracts/tuning.md`](../spec/contracts/tuning.md)
