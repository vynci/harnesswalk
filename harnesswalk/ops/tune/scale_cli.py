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


def _fix_clear_sms_approve(s: dict[str, Any]) -> None:
    """An approved SMS clear: the reply must give the number of messages, not the read id.
    Haiku, Sonnet and the 27B all report "pending, N messages" without the id (the sanity pass
    of 2026-11-16), and the count is the fact the user needs."""
    row = next(iter(s["rows"]["clear_sms"].values()))
    s["case"]["expect"]["answer_contains"] = [str(row["messages_to_clear"])]


FIXES = {"clear_sms_approve": _fix_clear_sms_approve}


def freeze_testset(
    src: Path, out: Path, drop_kinds: str, drop_cases: str, fix_kinds: str = ""
) -> dict[str, Any]:
    """A frozen copy of a test set without the dropped kinds (`kind,kind`) and cases
    (`pack/case_id,...`), with the expectation fixes of `fix_kinds` (FIXES) applied, and a
    manifest that names its parent, what was dropped and what was fixed."""
    kinds = {k.strip() for k in (drop_kinds or "").split(",") if k.strip()}
    cases = {c.strip() for c in (drop_cases or "").split(",") if c.strip()}
    fixes = [k.strip() for k in (fix_kinds or "").split(",") if k.strip()]
    unknown = [k for k in fixes if k not in FIXES]
    if unknown:
        raise SystemExit(f"no fix for {unknown}; known: {sorted(FIXES)}")
    scenarios = load_testset(src)
    keep = [
        s for s in scenarios
        if s["kind"] not in kinds and f"{s['pack']}/{s['case']['id']}" not in cases
    ]  # fmt: skip
    for s in keep:
        if s["kind"] in fixes:
            FIXES[s["kind"]](s)
    parent = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    by_pack: dict[str, dict[str, int]] = {}
    for s in keep:
        by_pack.setdefault(s["pack"], {}).setdefault(s["kind"], 0)
        by_pack[s["pack"]][s["kind"]] += 1
    manifest = {
        **{k: parent[k] for k in ("seed", "held_out_packs", "id_ranges") if k in parent},
        "parent_hash": parent.get("hash"), "hash": sc.scenario_hash(keep), "cases": len(keep),
        "dropped_kinds": sorted(kinds), "dropped_cases": sorted(cases), "fixed_kinds": fixes,
        "dropped": len(scenarios) - len(keep),
        "by_pack": {p: sum(k.values()) for p, k in by_pack.items()}, "by_kind": by_pack,
        "approval_cases": sum(1 for s in keep if "approval" in s["case"].get("tags", [])),
        "multi_turn_cases": sum(1 for s in keep if s["case"].get("turns")),
    }  # fmt: skip
    gen.write_jsonl(out / "scenarios.jsonl", keep)
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
        if args.testset:  # freeze: the given test set without the dropped kinds and cases
            manifest = freeze_testset(
                Path(args.testset), out, args.drop_kinds, args.drop_cases, args.fix_kinds
            )
        else:
            manifest = build_testset(h, out, args.seed if args.seed is not None else TEST_SEED)
        print(json.dumps(manifest, indent=2))
        return 0
    if not args.state:
        raise SystemExit(f"tune {args.action} needs --state DIR")
    state = Path(args.state)
    if args.report:  # --state A,B: B's results replace A's for the cases B ran
        only = None
        if args.testset:
            only = {f"{s['pack']}/{s['case']['id']}" for s in load_testset(Path(args.testset))}
        states = [Path(x) for x in args.state.split(",") if x.strip()]
        done = sc.merged(states) if len(states) > 1 else None
        print(json.dumps(sc.summary(states[0], done, only=only), indent=2))
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
        extra = None
        if args.ollama_tag:  # a tuned model by its Ollama tag, served like the alias it tunes
            base = dict(h.settings.get(f"models.models.{args.like}", None) or {})
            if base.get("provider") != "ollama":
                raise SystemExit(f"--like {args.like} is not an ollama alias")
            extra = {args.model: {**base, "name": args.ollama_tag, "label": args.model}}
        job = sc.Job(
            extra_models=extra,
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
            val_max=args.val_max,
        )  # fmt: skip
        print(json.dumps(out, indent=2, default=str))
    return 0
