"""`harnesswalk tune testset | scale | testrun` (P6): the command-line side of scale.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harnesswalk.ops.tune import scale as sc
from harnesswalk.ops.tune import scenarios as gen
from harnesswalk.ops.tune.curate import prompts_key

TEST_SEED = 61


def golden_prompts(h: Any) -> set[str]:
    """Every user message of every discovered pack's golden set."""
    from harnesswalk import packs as packs_mod
    from harnesswalk.ops.eval.golden import load_golden
    from harnesswalk.ops.tune.curate import prompts_of

    out: set[str] = set()
    for info in packs_mod.discover(h.settings).values():
        path = info.path / "golden.jsonl"
        if path.is_file():
            out |= prompts_of(load_golden(path))
    return out


def build_testset(h: Any, out: Path, seed: int = TEST_SEED) -> dict[str, Any]:
    """The test set: generated scenarios of the three governed packs, split `test`, with a
    manifest of counts, id ranges, and the checks that it shares no prompt with any golden
    set (the training split is checked against it when a curation starts)."""
    datasets = h.settings.root / "datasets"
    scenarios: list[dict[str, Any]] = []
    for pack, n in gen.TEST_COUNTS.items():
        scenarios += gen.generate(pack, "test", n, seed, datasets)
    golden = golden_prompts(h)
    clash = sorted(gen.prompts_of(scenarios) & golden)
    if clash:
        raise SystemExit(f"test prompts equal golden prompts: {clash[:5]}")
    by_pack: dict[str, dict[str, int]] = {}
    for s in scenarios:
        by_pack.setdefault(s["pack"], {}).setdefault(s["kind"], 0)
        by_pack[s["pack"]][s["kind"]] += 1
    manifest = {
        "seed": seed, "cases": len(scenarios), "hash": sc.scenario_hash(scenarios),
        "by_pack": {p: sum(k.values()) for p, k in by_pack.items()}, "by_kind": by_pack,
        "held_out_packs": list(gen.HELD_OUT), "id_ranges": gen.RANGES,
        "approval_cases": sum(1 for s in scenarios if "approval" in s["case"].get("tags", [])),
        "multi_turn_cases": sum(1 for s in scenarios if s["case"].get("turns")),
        "golden_prompt_overlap": 0,
        "entity_ids": len(gen.entity_ids(scenarios)),
    }  # fmt: skip
    gen.write_jsonl(out / "scenarios.jsonl", scenarios)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_testset(path: Path) -> list[dict[str, Any]]:
    file = path / "scenarios.jsonl"
    if not file.is_file():
        raise SystemExit(f"no test set at {path} (harnesswalk tune testset --out {path})")
    return gen.read_jsonl(file)


async def run(args: Any, h: Any, say: Any) -> int:
    t = h.tune
    if args.action == "testset":
        out = Path(args.out or "data/p6/testset")
        manifest = build_testset(h, out, args.seed if args.seed is not None else TEST_SEED)
        print(json.dumps(manifest, indent=2))
        return 0
    if not args.state:
        raise SystemExit(f"tune {args.action} needs --state DIR")
    state = Path(args.state)
    if args.report:
        print(json.dumps(sc.summary(state), indent=2))
        return 0
    if args.action == "testrun":
        if not args.model or not args.testset:
            raise SystemExit("tune testrun needs --model and --testset")
        scenarios = load_testset(Path(args.testset))
        if args.pack:
            scenarios = [s for s in scenarios if s["pack"] in args.pack]
        others = [Path(p) for p in args.only_failed.split(",") if p.strip()]
        if others:
            bad = sc.failed_by_all(others)
            scenarios = [s for s in scenarios if f"{s['pack']}/{s['case']['id']}" in bad]
            say(f"{len(scenarios)} cases failed by every one of {', '.join(map(str, others))}")
        job = sc.Job(
            mode="eval", state=state, model=args.model, scenarios=scenarios,
            workers=args.workers, max_usd=args.max_usd, prior_usd_per_case=args.prior_usd,
            prompt_arm=False, prompt_cache=args.prompt_cache, tenant=args.tenant,
            max_shards=args.max_shards,
        )  # fmt: skip
        totals = await sc.run(t, job, say)
        print(json.dumps(totals, indent=2))
        return 0
    # scale
    teacher = args.teacher or h.settings.get("tune.teacher", "local-27b")
    packs = args.pack or list(gen.TRAIN_PACKS)
    held = [p for p in packs if p in gen.HELD_OUT or p not in gen.TRAIN_PACKS]
    if held:
        raise SystemExit(f"{held} is held out or has no training split")
    seed = args.seed if args.seed is not None else 7
    datasets = h.settings.root / "datasets"
    if args.no_run and (state / "scenarios.jsonl").is_file():
        scenarios = gen.read_jsonl(state / "scenarios.jsonl")
    else:
        n = args.scenarios or 100
        scenarios = [s for p in packs for s in gen.generate(p, "train", n, seed, datasets)]
    exclude_prompts = golden_prompts(h)
    exclude_ids: set[str] = set()
    if args.testset:
        test = load_testset(Path(args.testset))
        check = gen.check_disjoint(scenarios, test)
        if check["n_shared_prompts"] or check["n_shared_ids"]:
            raise SystemExit(f"training scenarios overlap the test set: {check}")
        exclude_prompts |= gen.prompts_of(test)
        exclude_ids = gen.entity_ids(test)
    clash = {prompts_key(p) for p in exclude_prompts} & gen.prompts_of(scenarios)
    if clash:
        say(f"{len(clash)} generated prompts equal a golden prompt; their examples are dropped")
    if not args.no_run:
        job = sc.Job(
            mode="curate", state=state, model=teacher, scenarios=scenarios, workers=args.workers,
            max_usd=args.max_usd, prior_usd_per_case=args.prior_usd,
            target_kept=args.target_kept, variants=args.variants or 0, seed=seed,
            prompt_arm=not args.no_teacher_prompt_arm, prompt_cache=args.prompt_cache,
            tenant=args.tenant, max_shards=args.max_shards,
        )  # fmt: skip
        totals = await sc.run(t, job, say)
        print(json.dumps(totals, indent=2))
    if args.finalize:
        sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
        out = sc.finalize(
            t, state, args.finalize.removeprefix("dataset/"), tenant=args.tenant,
            exclude_prompts=exclude_prompts, exclude_ids=exclude_ids, sizes=sizes,
        )  # fmt: skip
        print(json.dumps(out, indent=2, default=str))
    return 0
