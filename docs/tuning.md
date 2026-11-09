# Tuning a local model

The tuning loop of spec 0.2.0 ([spec/contracts/tuning.md](../spec/contracts/tuning.md)), run
on the fake trainer in CI and on a homelab GPU for real. This page says how the numbers are
made; the measured run on qwen3.5:4b is added with the homelab worker (P3b).

## What is measured

The target behaviour comes from the scorecard ([scorecard.md](scorecard.md)): on the
approval-gated packs (`ecommerce`, `logistics`, profile `governed`) the local models look the
record up and then ask the user for approval in prose instead of calling the write tool and
letting the harness's approval queue decide ("write tool not called"), and the small qwen3.5
models call tools when none is needed ("called when none expected").

- **Target packs**: `ecommerce`, `logistics`. The `model` suite (deterministic scorers, model
  grading), 2 samples, temperature 0, each pack on its own profile.
- **Forgetting canary**: `sports` (grounded) and `_starter` (operated) must not drop by more
  than 1 case (mean of the samples).
- **Three arms, the same day**: the base (`local-4b`), the tuned model as Ollama serves it
  (the Q4_K_M GGUF built from the merge), and the prompt arm (the base with
  `config/tune/prompt_arm.md` appended to the pack's system prompt).
- **The gate**: the tuned model gains at least `tune.gate.min_gain_cases` (2) cases summed over
  the target packs and loses at most `tune.gate.canary_tolerance_cases` (1) on each canary pack.

## How the dataset is made

`harnesswalk tune curate` runs the teacher (`local-27b`, qwen3.5:27b, thinking off) through
the whole harness on every golden prompt of the target packs and on six teacher rewrites of
each (seeded, temperature 0, identifiers kept), with the prompt arm's text in its system
prompt. Only the turns every deterministic scorer passed are kept; each becomes one chat
example with the system prompt and context exactly as the harness built them, minus the prompt
arm's text (the student learns the behaviour without the instruction). Duplicates are removed
and a tenth of the examples, chosen by hash, is the validation split. The packs are synthetic,
so no customer data is in the dataset.

The golden prompts are both trained on and tested on, so the target numbers measure how well
the model learned the packs' own cases, not unseen traffic; the canary packs are never in the
dataset and measure forgetting.

## Run it

On the fake trainer, offline, in seconds: [walkthrough.md](walkthrough.md), "The tuning loop on
the command line". For real: [deploy/homelab/README.md](../deploy/homelab/README.md).
