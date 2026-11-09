# Evals (yours)

`make eval` runs the golden set of `packs/@@pack@@/golden.jsonl` through this harness on the
fake provider: each case replays its fixture from `packs/@@pack@@/fixtures/fake/` and is scored
on the walk (`path`), the tool calls, the answer, the citations, the schema and personal-data
leaks. CI runs it on every push (`.github/workflows/ci.yml`); a red golden set blocks a merge.

Add a case by writing its fixture first (what the model says, call by call), then a line in
`golden.jsonl` (format: the Harnesswalk spec, `spec/packs.md` and `spec/contracts/evaluator.md`).
When you delete a node later, this is what tells you whether anything depended on it.
