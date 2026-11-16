# Tuning a local model

The tuning loop of spec 0.2.0 ([spec/contracts/tuning.md](../spec/contracts/tuning.md)) run for
real once, on 2026-11-09: a LoRA adapter for qwen3.5:4b (`local-4b`), trained on the homelab
from the passing turns of qwen3.5:27b, gated against the base and a prompt-only control arm,
then canaried through the router. The same loop runs offline on the fake trainer
([walkthrough.md](walkthrough.md), "The tuning loop on the command line"); this page is the
measured run. A second round on 2026-11-15 retrained the recipe twice, built a second adapter
from three governed packs, and set the adapters beside the untuned 9B and Claude Haiku 5.5 with
GPU traces of every stage: [2026-11-15: the evidence round](#2026-11-15-the-evidence-round).

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

## 2026-11-15: the evidence round

Five questions, answered with runs on one day, the same harness and the same Ollama (0.34.1,
pinned to GPU 0; training on GPU 1). Every file is in
[tuning/2026-11-15/](tuning/2026-11-15/README.md): the scorecard JSON per arm, the loss series,
the GPU CSVs with their stage sidecar, and the script that summarizes them. The 2026-11-09 adapter
is called v1 below; its training numbers are carried over from that day, its pass counts were
measured again.

### The arms

| Arm | Alias | Trained on | Golden cases in its data | Held out (never in its data) |
|---|---|---|---|---|
| base 4B | `local-4b` (qwen3.5:4b Q4_K_M) | | | |
| v1 | `tuned-approvals-4b` (`tr_2`, 2026-11-09) | `ecommerce`, `logistics` | 22 of 193 examples | `device_fleet`, `_starter` |
| rerun 1 | `tuned-approvals-4b-r1` (`tr_3`) | the same dataset, seed 7 as v1 | the same | the same |
| rerun 2 | `tuned-approvals-4b-r2` (`tr_4`) | the same dataset, seed 11 | the same | the same |
| v2 | `tuned-approvals-v2-4b` (`tr_5`) | `ecommerce`, `logistics`, `device_fleet` rewrites (`--no-golden`) | none | `_starter` |
| v2l | `tuned-approvals-v2l-4b` (`tr_7`) | the v2 dataset, 6k sequence cap | none | `_starter` |
| 9B | `local-9b` (qwen3.5:9b Q4_K_M, untuned) | | | |
| Haiku | `hosted-haiku` (Claude Haiku 5.5, untuned) | | | |

`_starter` is the only approval-gated pack no adapter has seen (its `cancel_order` is
destructive and needs approval; 1 of its 16 cases is approval-gated), so it is a weak held-out
check. Correction to the 2026-11-09 text above: `_starter` does have an approval-gated action;
`sports` has none. The v2 dataset (`dataset/approvals_v2`, the new `tune curate --no-golden`)
holds teacher rewrites only: 726 scenarios, 235 kept (ecommerce 118, logistics 52, device_fleet
65), 197 train and 38 validation examples; no user message in it is a golden prompt. The
rewrites still share the golden cases' identifiers and expected answers, so v2's numbers on its
three packs are in-distribution, not unseen traffic.

v2 did not see what its dataset holds: the worker drops a conversation longer than its 4,096
token cap, and 38 of the 52 device_fleet training examples run to 4,100 to 5,200 tokens (big tool
tables and long tool results). v2 trained on 14 device_fleet examples. Raising the cap to 6,144
ran the card out of memory at step 13 (`tr_6`; the logits of a 5k-token prompt over the 248k
vocabulary). v2l is the same data with the cap at 6,144 and the new worker option
`tail_logits` (logits only over the reply; the same loss, about half the memory), so it trained
on every example.

### Question 1: why not a bigger model?

Pass counts on the four approval-gated packs, three samples per cell, mean with the range in
brackets (`compare/*.json`):

| Pack | base 4B | v1 | rerun 1 | rerun 2 | v2 | v2l | 9B | Haiku |
|---|---|---|---|---|---|---|---|---|
| `ecommerce` /24 | 4 (3-5) | **16** | 13 | 13 | 14 (13-15) | 13.7 (13-14) | 7 | 14.7 (14-15) |
| `logistics` /24 | 4 | 8 | 6.3 (6-7) | 6 | 6 | 5.7 (5-6) | **9.7** (9-10) | 7 (6-8) |
| `device_fleet` /28 | 5 | 6 | 5 | 6.7 (6-7) | 6 | 7.3 (7-8) | 6.7 (6-7) | **10** (9-11) |
| `_starter` /16 | 7 | 8 | 6 | 7.3 (7-8) | 7.3 (7-8) | **9** | 7 | 8.7 (8-9) |
| **All** /92 | 20 (19-21) | 38 | 30.3 (30-31) | 33 | 33.3 (33-34) | 35.7 (35-36) | 30.3 (30-31) | **40.3** (39-42) |

Speed and cost over the same 276 case runs per arm (a case is one full harness turn, every
model call of it; decode tokens per second is Ollama's own `eval_count / eval_duration`; GPU
energy is GPU 0's mean power times the arm's wall time, the card only):

| Arm | Median s per case | p95 s per case | Decode tokens/s | GPU energy per case | Price per case |
|---|---|---|---|---|---|
| base 4B | 1.24 | 2.07 | 158 | 0.11 Wh | 0 |
| v1 | 1.36 | 2.46 | 156 | 0.12 Wh | 0 |
| v2l | 1.18 | 2.37 | 157 | 0.11 Wh | 0 |
| 9B | 1.33 | 3.76 | 110 | 0.15 Wh | 0 |
| Haiku | 2.65 | 5.24 | not reported | | 0.0011 USD (0.105 USD per four-pack sample) |

The tuned 4B models serve at the base's speed (the merge changes weights, not shape). The 9B
decodes 30% slower and its slow cases are slower (p95 3.8 s). Haiku over the internet takes
twice the 4B's time per case. On these packs one tuned 4B (v1, 38) gets within a few cases of
Haiku (40.3) and beats the untuned 9B (30.3); v1's reruns and v2 land at the 9B's level or a
little above. What the tuned models fix is the targeted behaviour, not the packs: on ecommerce
"write tool not called" goes from 10.3 per sample on the base to 0 on four adapters and 0.7 on
v2l (the 9B still has 11, Haiku 4), while on logistics, whose failures are mostly fixture
wording, the 9B stays ahead. On all ten packs (question 5) the order holds: v1 111.5 and v2l
116 of 232 against the 9B's 102.5 and Haiku's 133.

### Question 2: does it reproduce?

| Run | Seed | Samples train/val | Steps | Final train loss | Final val loss | Train time | Peak (torch) | All /92 |
|---|---|---|---|---|---|---|---|---|
| v1 `tr_2` (2026-11-09, carried) | 7 | 322/31 | 82 | 0.0917 | 0.1045 | 23.1 min | 19.85 GiB | 38 |
| rerun 1 `tr_3` | 7 | 322/31 | 82 | 0.0917 | 0.1077 | 23.1 min | 19.85 GiB | 30.3 |
| rerun 2 `tr_4` | 11 | 322/31 | 82 | 0.0898 | 0.1036 | 23.1 min | 19.85 GiB | 33 |
| v2 `tr_5` | 7 | 293/50 (39 and 8 examples dropped) | 74 | 0.0998 | 0.0990 | 22.9 min | 20.35 GiB | 33.3 |
| `tr_6` (6k cap, full logits) | 7 | 368/65 | failed at step 13, out of memory | | | | | |
| v2l `tr_7` | 7 | 368/65 | 92 | 0.0936 | 0.0788 | 28.8 min | 11.04 GiB | 35.7 |

Train time is the `train` phase; merge, GGUF conversion, quantization and `ollama create` add
about a minute (`runs/*.json`, `pipeline_wall_s`). v2 and v2l validate on their own dataset,
so their validation loss does not compare with the first three rows.

The direction reproduces, the size only in part. Both reruns lift ecommerce from 4 to 13 (v1:
16) and remove the "write tool not called" failure there, and both lift logistics by 2 (v1: 4).
The same seed is not the same model: rerun 1's per-step training loss stays within 0.016 of
v1's and its mean training loss matches to four decimals, but its validation loss, its GGUF
hash and its answers differ (GPU kernels are not deterministic), and it is 8 cases below v1 on
the four packs. v1 looks like a good draw from a
spread of roughly 30 to 38 on these packs.

The base moves too. Temperature 0 is not reproducible across runs here: within one scorecard
run the samples of a cell mostly agree, across runs they do not. The same unchanged base 4B
scored 4 and then 2 on logistics in two runs today, and 1.5, 4.5 and 5 in three runs on
2026-11-09; v2l scored 7.3 and 9.5 on device_fleet in two runs an hour apart. Treat a difference
of 2 cases in one pack as noise.

### Question 3: does wider curation fix the held-out regression?

The regression itself did not reproduce: on `device_fleet` v1 scored 6 today against the base's
5 (2026-11-09: 4 against 7.5). What did reproduce is the behaviour behind it: v1 calls no tool
in 12 of device_fleet's failed cases per sample (2026-11-09: 13), the base in 10.

Wider curation helps when the training really holds the new pack. v2, which lost most of its
device_fleet examples to the length cap, is where v1 is (6/28, 9 "none called"). v2l, with every
example, moves the behaviour: "none called" drops to 4 and "write tool not called" to 0 (Haiku:
2.3 and 3.3), the failures of `tool_calls` fall from 14 to 7 and of `path` from 13 to 6 (Haiku 7.7
and 7). Its pass count gains less (7.3 and 9.5 in two runs, base 5 and 6, Haiku 10 and 12),
because 15.7 of its device_fleet cases still fail `answer_match`. On the one approval-gated pack
none of them saw, `_starter`, v2l scores 9/16 against the base's 7 and Haiku's 8.7 (ten-pack run:
9 against 6 and 9.5); with one approval case in that pack, it is a weak signal. A clean
generalisation test needs a second governed pack with unseen tools, which the repo does not
have.

### Question 5: what did the adapters cost elsewhere?

All ten packs, two samples per cell (`collateral/*.json`):

| Pack | Seen by | base 4B | v1 | v2 | v2l | 9B | Haiku |
|---|---|---|---|---|---|---|---|
| `_starter` /16 | none | 6 | 8 | 7.5 (7-8) | 9 | 7 | 9.5 (9-10) |
| `device_fleet` /28 | v2, v2l | 6 (5-7) | 6 | 6 | 9.5 (9-10) | 5 | 12 (11-13) |
| `ecommerce` /24 | all adapters | 4.5 (4-5) | 15 | 13 | 14 | 7 | 14.5 (14-15) |
| `finance` /25 | none | 7 | 12 | 11 | 12 | 14 | 17 |
| `gov_services` /27 | none | 10.5 (10-11) | 15 | 15 | 16 | 14.5 (14-15) | 15.5 (15-16) |
| `healthcare` /25 | none | 7 | 9 | 11 | 9 | 7.5 (7-8) | 10 |
| `helpdesk_lite` /21 | none | 16 | 15 | 16 | 15 | 17 | 19 |
| `logistics` /24 | all adapters | 2 | 7.5 (7-8) | 6 | 6.5 (5-8) | 8 (7-9) | 7 |
| `long_session` /20 | none | 9 | 11 | 11.5 (11-12) | 9 | 10.5 (10-11) | 13.5 (13-14) |
| `sports` /22 | none | 12 | 13 | 13 | 16 | 12 | 15 |
| **All** /232 | | 80 (78-82) | 111.5 (111-112) | 110 | **116** (115-117) | 102.5 (102-103) | **133** (132-134) |
| The seven packs no adapter saw /156 | | 67.5 | 83 | 85 | 86 | 82.5 | 99.5 |

Haiku's ten packs cost 0.22 USD per sample. On the seven packs no adapter saw, every adapter
scores where the untuned 9B does (83 to 86 against 82.5), all well above the base (67.5) and
below Haiku (99.5).

The adapters gain on packs they never saw, mostly by calling fewer tools: on `finance` the base
calls a tool where none is expected in 15 cases, v1 in 2. The losses are small: `helpdesk_lite`
drops by 1 on v1 and v2l (two new "none called" cases), `long_session` is flat on v2l.

### Question 4: what the hardware does

`gpu/summary.json`, from `nvidia-smi` at 1 Hz (`deploy/homelab/gpulog.py`). GPU 0 serves Ollama,
GPU 1 trains; stages overlapped (training on GPU 1 while GPU 0 curated or evaluated), so read
each stage by its own card:

| Stage | GPU | Minutes | Util mean / p95 | Memory peak | Power mean / peak | Temp peak | Energy |
|---|---|---|---|---|---|---|---|
| Curation, 27B teacher (`curate_v2_27b`) | 0 | 81.6 | 93% / 100% | 21.0 GiB | 308 / 352 W | 83 C | 418 Wh |
| Training, rerun 1 (`train_repro_seed7`) | 1 | 24.3 | 94% / 100% | 22.2 GiB | 332 / 351 W | 76 C | 134 Wh |
| Training, rerun 2 (`train_repro_seed11`) | 1 | 24.2 | 95% / 100% | 22.2 GiB | 333 / 351 W | 76 C | 134 Wh |
| Training, v2 (`train_v2`) | 1 | 24.1 | 94% / 100% | 22.7 GiB | 331 / 351 W | 76 C | 133 Wh |
| Training, 6k cap, full logits (out of memory) (`train_v2_long`) | 1 | 4.7 | 87% / 100% | 23.6 GiB | 310 / 350 W | 77 C | 24 Wh |
| Training, v2l (6k cap, tail logits) (`train_v2_long2`) | 1 | 30.0 | 95% / 100% | 16.8 GiB | 336 / 351 W | 77 C | 168 Wh |
| Inference, base 4B, 4 packs x 3 (`d_local_4b`) | 0 | 5.8 | 78% / 100% | 22.0 GiB | 316 / 348 W | 87 C | 31 Wh |
| Inference, v2l, 4 packs x 3 (`d_tuned_approvals_v2l_4b`) | 0 | 6.0 | 76% / 100% | 3.9 GiB | 305 / 334 W | 79 C | 30 Wh |
| Inference, 9B, 4 packs x 3 (`d_local_9b`) | 0 | 7.7 | 84% / 100% | 10.5 GiB | 315 / 347 W | 82 C | 40 Wh |
| Inference, 9B, 10 packs x 2 (`e_local_9b`) | 0 | 15.0 | 82% / 100% | 7.2 GiB | 312 / 346 W | 78 C | 78 Wh |
| Idle (`idle_end`) | 0 | 1.0 | 0% / 0% | 0.2 GiB | 30 / 84 W | 65 C | 0 Wh |
| Idle (`idle_end`) | 1 | 1.0 | 0% / 0% | 0.0 GiB | 9 / 10 W | 28 C | 0 Wh |

- **Curation** (qwen3.5:27b Q4_K_M teacher, 81.6 min: 9 min writing the rewrites, then 72.5 min
  running 726 scenarios through the harness, about 6 s each): GPU 0 at 93% for the whole stage, 308 W on average, 83 C at the
  peak, 0.42 kWh.
- **Training** (LoRA bf16, batch 1 x 8): GPU 1 pinned at 94 to 95% and 331 to 336 W (the card's
  limit is 350 W) for 24 to 30 minutes, 76 to 77 C; `nvidia-smi` shows 22.7 GiB in use where torch
  reports a 19.85 GiB peak (the allocator's reserve). With `tail_logits` v2l peaked at 11.04 GiB
  on a longer dataset. One training run is about 0.13 to 0.17 kWh on the card.
- **Inference** (scorecard runs, 4B and 9B): GPU 0 at 75 to 84%, about 310 to 320 W; the gaps are
  the harness between model calls. A 4B model holds about 4 GiB (32k context), the 9B 7.2 GiB in a clean
  stage; a stage's peak can include a model the previous stage left loaded (the base 4B's 22 GiB is the 27B
  teacher still resident after curation).
- `gpu/nvtop-train_repro_seed7.txt` is three minutes of `nvtop` text during rerun 1's training,
  with GPU 1 alone busy.

### What this round does and does not show

- Measured on 2026-11-15: every pass count above, the reruns' and v2's training, the GPU
  traces, the latencies and Haiku's cost. Carried from 2026-11-09: v1's training run (loss, time,
  memory) and the first dataset.
- Small n: three samples per cell (two for the ten packs) on 16 to 28 cases a pack, one day,
  and run-to-run drift of about 2 cases per pack at temperature 0 (question 2).
- ecommerce and logistics are trained-on packs for every adapter (v1's data even holds 22
  golden cases), and device_fleet is one for v2 and v2l (rewrites only): those cells measure
  learning these packs, not unseen traffic. Only `_starter` and the six non-governed packs are
  unseen by all adapters.
- The cost comparison counts the GPU card's energy only (no CPU, no idle, no purchase) against
  Haiku's API price, and the 4B's hardware is already paid for. Curation and one training run
  cost about 0.4 and 0.15 kWh on the cards.

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

### The scaled runs (P6)

Generated scenarios with new entities and constructed expectations
(`harnesswalk/ops/tune/scenarios.py`), a 1,000-case test set that shares no prompt and no
entity id with the training split (device_fleet is test only), and a sharded runner that
resumes from its state directory and stops before a spend cap (`harnesswalk/ops/tune/scale.py`):

```sh
uv run harnesswalk tune testset --out data/p6/testset
uv run harnesswalk tune scale --teacher hosted-haiku --state data/p6/curate/haiku \
  --scenarios 6000 --variants 1 --workers 4 --max-usd 30 --target-kept 10000 \
  --testset data/p6/testset --prompt-cache --finalize p6_haiku --sizes 200,1000,5000
uv run harnesswalk tune testrun --testset data/p6/testset --model local-4b \
  --state data/p6/eval/local-4b
uv run harnesswalk tune testrun --state data/p6/eval/local-4b --report
```

Run the same command again to resume; finished shards are read, not run again.
