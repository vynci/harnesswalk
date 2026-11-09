# Tuning a local model

The tuning loop of spec 0.2.0 ([spec/contracts/tuning.md](../spec/contracts/tuning.md)) run for
real once, on 2026-11-09: a LoRA adapter for qwen3.5:4b (`local-4b`), trained on the homelab
from the passing turns of qwen3.5:27b, gated against the base and a prompt-only control arm,
then canaried through the router. The same loop runs offline on the fake trainer
([walkthrough.md](walkthrough.md), "The tuning loop on the command line"); this page is the
measured run.

## The result

`model`-suite pass counts (deterministic scorers, model grading), two samples per cell,
temperature 0, each pack on its own profile, all three arms on the same day, the same harness
and the same Ollama. The tuned column is the Q4_K_M model Ollama serves
(`hw-approvals-4b:tr_2`), not the bf16 weights.

| Pack | Role | Base `local-4b` | Tuned `model/approvals_4b` | Prompt arm (base + `config/tune/prompt_arm.md`) |
|---|---|---|---|---|
| `ecommerce` | target | 4/24 | **14/24** | 6/24 (5 to 7) |
| `logistics` | target | 4.5/24 (3 to 6) | **8/24** | 3.5/24 (3 to 4) |
| `sports` | canary | 12/22 | 13/22 | 12/22 |
| `_starter` | canary | 7/16 | 8/16 | 6/16 |

**Gate: passed.** The tuned model gains 13.5 cases on the two target packs (the gate asks for
2) and loses nothing on either canary pack (each moves up by one; the gate allows a loss of 1).
The prompt arm gains 1 case: it would not pass the same gate.

The behaviour the loop was aimed at, the `tool_calls` split of the failed cases on the target
packs (mean per sample):

| Bucket | Base | Tuned | Prompt arm |
|---|---|---|---|
| write tool not called | 12.5 | **0** | 6 |
| called when none expected | 2 | 0 | 1 |
| no tool called | 0 | 1 | 1 |
| read tool not called | 4 | 4 | 4.5 |

The base looks the order or shipment up and then asks the user for approval in prose; the tuned
model calls `cancel_order`, `issue_refund` or `reroute` and lets the harness's approval node
decide. What still fails on the tuned model is mostly `answer_match` (8 of 10 failed cases on
ecommerce, 15 of 16 on logistics): its replies are right but not worded the way the fixtures
are, the golden-design problem the scorecard already notes for logistics. Citations on
ecommerce went from 5 failing cases to none.

The prompt arm halves "write tool not called" (12.5 to 6) but the gain stops there: on logistics
it makes the model call tools when none is needed and lose answers. Words help this 4B model a
little; weights fix the behaviour. The text is in `config/tune/prompt_arm.md`.

## How it was made

### Dataset (`dataset/approvals@v1`)

- **Teacher**: `local-27b` (qwen3.5:27b, thinking off) through the whole harness, with the prompt
  arm's text in its system prompt (context distillation: the exported examples use the pack's
  own prompt). On the 48 golden prompts of the two packs it passed 22 with the prompt arm
  (ecommerce 16, logistics 6, 46%). The brief's rule was to switch to a hosted teacher below 70%;
  hosted Sonnet passed 25 of the same 48 (17 and 8, 0.52 USD plus 0.64 USD), so it would not
  clear 70% either: the misses of both are mostly `answer_match` against fixture phrasing, not
  behaviour. The local teacher was kept.
- **Scenarios**: the 48 golden prompts plus ten teacher rewrites of each (seed 7, temperature 0,
  identifiers kept; rewrites that dropped an id were discarded): 516 scenarios, run in chunks of
  24 per scratch tenant.
- **Kept**: 193 turns that passed every deterministic scorer (37%; ecommerce 133, logistics 60).
  Dropped by first failing scorer: `answer_match` 165, `path` 141, `citations` 13, `tool_calls` 4.
  No duplicates. Split by hash: 177 train, 16 validation examples.
- **Samples**: one per model call of a kept turn (the prompt exactly as Ollama renders the
  conversation so far, and that call's reply as the target), 322 train and 31 validation, none
  over 4,096 tokens (the longest 3,920); 1.0 M tokens in all, of which 20,131 are trained on.
- **Source**: dataset packs only, synthetic and CC0; no tenant's turns and no customer data. The
  train file is 2.0 MB, over the 2 MB the plan allows in git, so it is not committed: the
  manifest (hashes, counts, filters) is in the artifact store and `regenerate` rebuilds it.

### Training (`adapter/approvals_4b@v1`)

LoRA in bf16 on every linear layer of the text model (rank 16, alpha 32, dropout 0.05, 32.5 M
trainable parameters), 2 epochs, learning rate 2e-4 cosine with 5% warmup, batch 1 with 8
accumulation steps, gradient checkpointing, sequence cap 4,096: 82 steps on one RTX 3090
(peak 19.9 GiB), 23 minutes. Final train loss 0.092, validation loss 0.105. Base
`Qwen/Qwen3.5-4B` at revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`. A first attempt ran out
of memory at step 63 (a 4k-token sample's logits over a 248k vocabulary fragmented the CUDA
cache); expandable segments fixed it.

### Serving (`model/approvals_4b@v2`)

Merge in bf16, `convert_hf_to_gguf.py --no-mtp` (Ollama's own qwen3.5:4b has no multi-token
prediction layer; a GGUF that declares one does not load), `llama-quantize` Q4_K_M (2.7 GB),
`ollama create` with the base's `RENDERER qwen3.5`, `PARSER qwen3.5` and parameters. The
template check renders two validation prefixes with the worker's port of Ollama's renderer and
sends them both raw and through `/api/chat`: the same prompt token counts (2,597 and 2,788) and
the same replies. Training on the Hugging Face chat template instead would have been 173 tokens
off on a typical prompt (Ollama serializes tools through its own structs and drops the think
block of earlier assistant turns when thinking is off). The other route, the LoRA as a GGUF
`ADAPTER` over Ollama's qwen3.5:4b, is refused by Ollama 0.34.1 ("LoRA adapters are no longer
supported"); the merged GGUF is the route.

### Canary

`tune promote --fraction 0.5` added the alias `tuned-approvals-4b` and `router.canary` for
ecommerce and logistics. Fourteen real chat turns split 6 to the candidate and 8 to the base by
turn-id hash. Two base turns did the measured failure live ("I'm going to ask a support team
member for approval" on a cancellation, "I need your explicit approval" on a reroute) and were
thumbed down; the candidate's cancel, refund and reroute turns went through the approval node.
Canary gate: base 2 of 8 failed, candidate 0 of 6, passed; `--full` set the fraction to 1.0 and
the next cancellation ran on the tuned model; `tune rollback` set it to 0 and the same request
went back to the base, which asked in prose again.

### The scorecard column

The same two target packs and the held-out approval-gated pack `device_fleet` (never in the
dataset) on the scorecard, with the tuned alias as a column (`harnesswalk scorecard --models
local-4b,tuned-approvals-4b --packs ecommerce,logistics,device_fleet --samples 2`; the cell data
is [scorecard/2026-11-09-tuned.json](scorecard/2026-11-09-tuned.json)):

| Pack | `local-4b` | `tuned-approvals-4b` |
|---|---|---|
| `ecommerce` (governed, trained on) | 4.5/24 (4 to 5) | 12.5/24 (12 to 13) |
| `logistics` (governed, trained on) | 5/24 | 8.5/24 (8 to 9) |
| `device_fleet` (governed, held out) | 7.5/28 (7 to 8) | **4/28** |

The target numbers agree with the comparison within a case (a separate run, two more samples).
The held-out pack does not: on `device_fleet`, an approval-gated pack with tools the dataset
never showed (reboot, power-off, SMS clear), the tuned model calls no tool in 13 failed cases
against the base's 6, and still answers "I will clear the SMS inbox after your approval" instead
of calling `clear_sms`. **The adapter learned these two packs' actions, not the policy in
general, and it is worse than the base on a governed pack it has not seen.** The gate passed
because its canary packs (`sports`, `_starter`) have no approval-gated actions, so they measure
forgetting of plain answers and lookups, not this. A held-out approval-gated pack belongs among
the canary packs, and the next dataset should come from every governed pack but one.

## What this does and does not show

- The golden prompts are both trained on (with rewrites) and tested on, so the target numbers say
  how well the model learned these packs' cases, not unseen traffic. The canary packs and
  `device_fleet` were never in the dataset, and `device_fleet` shows the behaviour did not
  transfer (above).
- One run, one seed, two samples per cell. The canary demo had 14 turns and a human reviewer.
- The teacher's own pass rate caps what the dataset can teach: the student learned the
  behaviour the teacher showed on the turns it passed.

## Run it yourself

The commands, with the homelab's Ollama at `HARNESSWALK_OLLAMA_URL` and the worker set up
([deploy/homelab/README.md](../deploy/homelab/README.md)); run long jobs under `caffeinate -i`
on a Mac, or an idle machine sleeps in the middle of a run:

```sh
uv run harnesswalk tune curate --pack ecommerce --pack logistics --teacher local-27b \
  --variants 10 --seed 7 --out dataset/approvals
uv run harnesswalk tune run --dataset approvals --trainer homelab-trl --student local-4b --name approvals_4b
uv run harnesswalk tune compare --candidate model/approvals_4b --against local-4b \
  --packs ecommerce,logistics --canary-packs sports,_starter --samples 2 --out compare.md
uv run harnesswalk tune promote --candidate model/approvals_4b --fraction 0.5
uv run harnesswalk tune promote --candidate model/approvals_4b --full
uv run harnesswalk tune rollback
```

Wall time: curation 85 minutes (27B teacher, 516 scenarios; about 35 of those minutes the Mac
driving it was asleep), training with merge, conversion and `ollama create` 24 minutes, the
comparison 15 minutes (24 model-suite runs of the 4B models).
