# Starter datasets

Five small, synthetic packs so a learner can ask real questions in minute one. Each pack shows
off a different part of the harness on purpose. The packs are generated and committed in P5;
this file fixes their shape and the licence rule now.

## The five packs

| Pack | Domain content | Harness feature it demonstrates |
|---|---|---|
| `healthcare` | Clinic policies, a synthetic patient FAQ (no real PHI) | PII redaction in input guardrails, and refusal |
| `ecommerce` | Catalog, returns policy, order lookups via a tool | A destructive action behind approval (`cancel_order`) |
| `logistics` | Shipments, routes, a tracking tool | Multi-step tool use in the loop |
| `sports` | League rules, fixtures, a stats tool | Cheap-model routing for lookups |
| `finance` | Fund fact sheets, capital call notices (synthetic) | Citations from knowledge, and the memory writer |

## Pack layout

```
datasets/<pack>/
  PROVENANCE.md        required: how the pack was made (see below)
  system_prompt.md     skill zero
  skills/*.md          3 to 5 skills
  facts.jsonl          about 20 semantic facts
  episodes.jsonl       about 10 dated events
  docs/                10 to 20 documents (markdown, some PDF)
  tools.yaml           2 to 3 domain tools (format in spec/contracts/tool_registry.md)
  golden.jsonl         20 to 30 eval cases with expected answers and expected node paths
  prompts.md           "try these" prompts, each with what lights up in the diagram
```

Each pack stays under 2 MB.

## Licence and provenance rule

- **Content is CC0 1.0.** Everything under `datasets/<pack>/` except code is dedicated to the
  public domain. Code that generates or loads packs is Apache-2.0 like the rest of the repo.
- **Synthetic only.** No real people, patients, customers, accounts or companies. Names, ids and
  numbers are invented. Nothing is copied from a source with a different licence.
- **Provenance card required.** Every pack has a `PROVENANCE.md` that records: the generation
  script and its version, the model and provider used, the prompts (or a pointer to them in the
  repo), the date, the human review done, and a statement that the content is synthetic and CC0.
  A pack without a provenance card is not merged.
- **Reproducible.** Generation is scripted, so a pack can be regenerated and diffed.
- **Upgrade datasets are separate.** Real, permissively licensed sets (CC BY and similar) may be
  offered as optional downloads with their own licence and attribution. They are never
  committed into the CC0 packs.
