"""Scaled, resumable model-suite runs over generated scenarios (P6, the scale-up round).

Two jobs share one runner:

- **curate** (`harnesswalk tune scale`): training scenarios (`scenarios.py`, split `train`)
  plus teacher paraphrases run through the whole harness pinned to the teacher; the turns
  every deterministic scorer passed become examples, tagged with the teacher, the pack, the
  scenario kind and whether the prompt was generated or paraphrased. `finalize` writes the
  dataset (and nested subsets for a size curve) as `dataset/<name>` artifacts.
- **eval** (`harnesswalk tune testrun`): the test set (split `test`) on one model alias, for
  the held-out evaluation and the strong-model sanity check, with Wilson 95% intervals.

The work is cut into shards of about 24 scenarios of one pack. Each shard runs as one
`model`-suite eval run on a candidate view of the pack whose tool tables carry the shard's
generated rows. A finished shard is one JSON file in the state directory, written atomically,
so a run that stops (a sleeping laptop, a crash, the spend cap) resumes where it was: finished
shards are read, not run again, and paraphrases already paid for are cached per shard.

Parallel workers run shards concurrently in one process, each on its own tenant (the eval
runner allows one run per tenant). The spend meter adds the cost of every finished shard and
reserves an estimate for every shard in flight; a shard starts only while spent + reserved +
its own estimate stays under `max_usd`. The estimate is the observed cost per case (times 1.5)
once two shards are done, the `prior_usd_per_case` before that.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harnesswalk.ops.eval.golden import GoldenCase, normalize
from harnesswalk.ops.tune import curate as cur
from harnesswalk.ops.tune import scenarios as gen

if TYPE_CHECKING:
    from harnesswalk.ops.tune import Tuning

SHARD = 24
STATE_VERSION = 1


def wilson(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval of a pass rate (95% by default)."""
    if n <= 0:
        return 0.0, 0.0
    p = passed / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def shards_of(scenarios: list[dict[str, Any]], size: int = SHARD) -> list[dict[str, Any]]:
    """Scenarios in shards of one pack each, interleaved across packs, ids stable."""
    per_pack: dict[str, list[dict[str, Any]]] = {}
    for s in scenarios:
        per_pack.setdefault(s["pack"], []).append(s)
    queues = {
        pack: [items[i : i + size] for i in range(0, len(items), size)]
        for pack, items in per_pack.items()
    }
    out: list[dict[str, Any]] = []
    i = 0
    while any(queues.values()):
        for pack in sorted(queues):
            if queues[pack]:
                n = sum(1 for s in out if s["pack"] == pack) + 1
                out.append(
                    {"id": f"{pack}-{n:05d}", "pack": pack, "scenarios": queues[pack].pop(0)}
                )
        i += 1
    return out


def scenario_hash(scenarios: list[dict[str, Any]]) -> str:
    text = "".join(json.dumps(s, sort_keys=True) for s in scenarios)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


class Meter:
    """Spend so far, the reservations of shards in flight, and whether one more may start."""

    def __init__(self, max_usd: float, prior_usd_per_case: float) -> None:
        self.max_usd = float(max_usd)
        self.prior = float(prior_usd_per_case)
        self.spent = 0.0
        self.cases = 0
        self.shards = 0
        self.reserved: dict[str, float] = {}

    def per_case(self) -> float:
        if self.shards >= 2 and self.cases:
            return max(self.prior * 0.25, 1.5 * self.spent / self.cases)
        return self.prior

    def estimate(self, n_cases: int) -> float:
        return n_cases * self.per_case()

    def try_reserve(self, shard_id: str, n_cases: int) -> bool:
        est = self.estimate(n_cases)
        if self.spent + sum(self.reserved.values()) + est > self.max_usd:
            return False
        self.reserved[shard_id] = est
        return True

    def settle(self, shard_id: str, usd: float, n_cases: int) -> None:
        self.reserved.pop(shard_id, None)
        self.spent += float(usd)
        self.cases += n_cases
        self.shards += 1


@dataclasses.dataclass
class Job:
    mode: str  # "curate" or "eval"
    state: Path
    model: str  # the teacher (curate) or the evaluated alias (eval)
    scenarios: list[dict[str, Any]]
    workers: int = 1
    max_usd: float = 1.0
    prior_usd_per_case: float = 0.003
    target_kept: int | None = None
    variants: int = 0
    seed: int = 7
    prompt_arm: bool = True
    prompt_cache: bool = False
    tenant: str = "dev"
    max_shards: int | None = None
    shard_size: int = SHARD


def load_done(state: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted((state / "shards").glob("*.done.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out[data["shard"]] = data
    return out


def plan(job: Job) -> dict[str, Any]:
    """Write or check `plan.json` and `scenarios.jsonl`: a resumed job must be the same job."""
    job.state.mkdir(parents=True, exist_ok=True)
    digest = scenario_hash(job.scenarios)
    want = {
        "version": STATE_VERSION, "mode": job.mode, "model": job.model, "scenarios": digest,
        "n_scenarios": len(job.scenarios), "variants": job.variants, "seed": job.seed,
        "prompt_arm": job.prompt_arm, "shard_size": job.shard_size,
    }  # fmt: skip
    path = job.state / "plan.json"
    if path.is_file():
        have = json.loads(path.read_text(encoding="utf-8"))
        diff = {k: (have.get(k), v) for k, v in want.items() if have.get(k) != v}
        if diff:
            raise ValueError(f"{job.state} holds a different job: {diff}")
    else:
        _atomic(path, want)
        gen.write_jsonl(job.state / "scenarios.jsonl", job.scenarios)
    return want


def _cases(scenarios: list[dict[str, Any]]) -> list[GoldenCase]:
    return [normalize(s["case"], f"{s['pack']}/{s['case']['id']}") for s in scenarios]


async def _paraphrases(
    tuning: Tuning, job: Job, shard: dict[str, Any], cases: list[GoldenCase]
) -> dict[str, Any]:
    """The teacher's paraphrases of the shard's one-prompt cases, cached per shard."""
    path = job.state / "shards" / f"{shard['id']}.para.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    from harnesswalk.execution.model_layer import price

    texts: dict[str, list[str]] = {}
    usage = Counter()
    for case in cases:
        if case.multi_turn or job.variants <= 0:
            continue
        got, used = await cur.rewrites(tuning, job.model, case, job.variants, job.seed)
        texts[case.id] = got
        usage.update(used)
    cfg = tuning.h.settings.get(f"models.models.{job.model}", {}) or {}
    usd, _ = price(cfg, usage["input_tokens"], usage["output_tokens"])
    data = {"texts": texts, "usage": dict(usage), "usd": round(usd, 6)}
    _atomic(path, data)
    return data


def _models_text(tuning: Tuning, job: Job) -> str:
    text = tuning._at_temperature_zero(tuning._models_text(), [job.model])
    if job.prompt_cache:
        import yaml

        from harnesswalk.ops.tune.models_edit import with_alias

        cfg = dict((yaml.safe_load(text) or {}).get("models", {}).get(job.model) or {})
        cfg["params"] = {**(cfg.get("params") or {}), "prompt_cache": True}
        text = with_alias(text, job.model, cfg, f"{job.model} with prompt caching (P6)")
    return text


async def run_shard(tuning: Tuning, job: Job, shard: dict[str, Any], tenant: str) -> dict:
    """One shard: paraphrases (curate), one model-suite run on the pack with the shard's
    rows, the per-case results, and (curate) the kept examples. Returns the done record."""
    from harnesswalk.ops.eval.offline import read_tools_of
    from harnesswalk.ops.eval.scorecard import reduce_case

    started = time.time()
    pack = shard["pack"]
    base = _cases(shard["scenarios"])
    kind_of = {s["case"]["id"]: s["kind"] for s in shard["scenarios"]}
    para = {"texts": {}, "usage": {}, "usd": 0.0}
    cases = list(base)
    if job.mode == "curate":
        para = await _paraphrases(tuning, job, shard, base)
        texts = para["texts"]
        cases = cur.scenarios_for(base, texts)
        for case in cases:
            if case.id not in kind_of:
                kind_of[case.id] = kind_of[case.id.rsplit("_p", 1)[0]]
    arm = tuning.prompt_arm_text() if (job.mode == "curate" and job.prompt_arm) else ""
    tools_text = tuning.h.view(pack).release.store.get(pack, "tools", "tools").body
    changes: dict[tuple[str, str], str] = {
        ("models", "models"): _models_text(tuning, job),
        ("tools", "tools"): gen.tools_overlay(tools_text, [s["rows"] for s in shard["scenarios"]]),
    }
    base_prompt = tuning._prompt_text(pack)
    if arm and base_prompt is not None:
        changes[("prompt", "system_prompt")] = base_prompt.rstrip() + "\n\n" + arm
    view = tuning._view(pack, changes)
    examples: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    try:
        run = await tuning._model_run(tenant, pack, job.model, view, job.mode, cases)
        case_tenant = run.get("case_tenant") or f"evalrun-{run['run_id']}"
        read_tools = read_tools_of(view)
        originals = {c.id for c in base}
        for c in tuning.h.evals.store.cases(run["run_id"]):
            reduced = reduce_case(c, read_tools)
            actual = c.get("actual") or {}
            source = "generated" if c["case_id"] in originals else "paraphrase"
            reduced.update({
                "kind": kind_of.get(c["case_id"]), "source": source,
                "usd": ((actual.get("cost") or {}).get("usd") or 0.0),
                "tool_calls": actual.get("tool_calls") or [],
                "reply": (actual.get("reply") or "")[:600],
            })  # fmt: skip
            results.append(reduced)
            if job.mode != "curate" or c["status"] != "passed":
                continue
            for turn_id in c.get("turn_ids") or [c.get("turn_id")]:
                ex = cur.example_of(tuning, case_tenant, turn_id, view, arm) if turn_id else None
                if ex is None:
                    reduced.setdefault("dropped", []).append("no_content")
                    continue
                ex["meta"] = {
                    "pack": pack, "case_id": c["case_id"], "kind": reduced["kind"],
                    "source": source, "teacher": job.model,
                    "teacher_model": (tuning.h.settings.get(f"models.models.{job.model}", {})
                                      or {}).get("name", job.model),
                    "shard": shard["id"],
                }  # fmt: skip
                examples.append(ex)
        run_usd = float((run.get("cost") or {}).get("usd") or 0.0)
        run_id = run["run_id"]
        status = run.get("status")
    finally:
        view.close()
    usd = run_usd + float(para.get("usd") or 0.0)
    record = {
        "shard": shard["id"], "pack": pack, "mode": job.mode, "model": job.model,
        "run_id": run_id, "run_status": status, "cases": results, "examples": examples,
        "usd": round(usd, 6), "run_usd": round(run_usd, 6), "paraphrase": para,
        "seconds": round(time.time() - started, 1), "finished_at": time.time(),
    }  # fmt: skip
    if status == "done":  # an errored run is not done: the shard runs again on resume
        _atomic(job.state / "shards" / f"{shard['id']}.done.json", record)
    return record


async def run(tuning: Tuning, job: Job, say: Any = print) -> dict[str, Any]:
    """Every shard not done yet, `job.workers` at a time, inside the spend cap and the kept
    target. Returns the job's totals (done shards included)."""
    plan(job)
    shards = shards_of(job.scenarios, job.shard_size)
    done = load_done(job.state)
    meter = Meter(job.max_usd, job.prior_usd_per_case)
    kept = 0
    for rec in done.values():
        meter.settle(rec["shard"], rec["usd"], len(rec["cases"]))
        kept += len(rec["examples"])
    pending = [s for s in shards if s["id"] not in done]
    if job.max_shards is not None:
        pending = pending[: max(0, job.max_shards - len(done))]
    say(f"{len(done)} shards done, {len(pending)} to run, spent ${meter.spent:.4f} of"
        f" ${job.max_usd:.2f}, {kept} examples kept")  # fmt: skip
    queue: asyncio.Queue = asyncio.Queue()
    for s in pending:
        queue.put_nowait(s)
    stop = {"reason": None}
    lock = asyncio.Lock()

    def n_cases(shard: dict[str, Any]) -> int:
        k = len(shard["scenarios"])
        return k * (1 + job.variants) if job.mode == "curate" else k

    async def worker(i: int) -> None:
        nonlocal kept
        tenant = f"{job.tenant}-w{i}"
        while not queue.empty() and stop["reason"] is None:
            shard = queue.get_nowait()
            async with lock:
                if job.target_kept is not None and kept >= job.target_kept:
                    stop["reason"] = f"kept target {job.target_kept} reached"
                    return
                if not meter.try_reserve(shard["id"], n_cases(shard)):
                    stop["reason"] = (
                        f"spend cap: ${meter.spent:.4f} spent, "
                        f"${sum(meter.reserved.values()):.4f} in flight, the next shard"
                        f" ~${meter.estimate(n_cases(shard)):.4f}, cap ${job.max_usd:.2f}"
                    )
                    return
            try:
                rec = await run_shard(tuning, job, shard, tenant)
            except Exception as exc:  # a failed shard is reported and runs again on resume
                meter.reserved.pop(shard["id"], None)
                say(f"{shard['id']}: error {type(exc).__name__}: {exc}"[:300])
                continue
            meter.settle(shard["id"], rec["usd"], len(rec["cases"]))
            kept += len(rec["examples"])
            passed = sum(1 for c in rec["cases"] if c["status"] == "passed")
            say(f"{shard['id']}: {passed}/{len(rec['cases'])} passed, {len(rec['examples'])}"
                f" examples, ${rec['usd']:.4f} in {rec['seconds']:.0f}s; total ${meter.spent:.4f},"
                f" {kept} kept")  # fmt: skip

    await asyncio.gather(*(worker(i) for i in range(max(1, job.workers))))
    done = load_done(job.state)
    totals = summary(job.state, done)
    totals["stopped"] = stop["reason"]
    totals["remaining_shards"] = len([s for s in shards if s["id"] not in done])
    _atomic(job.state / "summary.json", totals)
    return totals


def summary(state: Path, done: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pass rates per pack and kind with Wilson intervals, spend, keep rate, failure split."""
    done = load_done(state) if done is None else done
    cases = [c for rec in done.values() for c in rec["cases"]]
    usd = sum(rec["usd"] for rec in done.values())
    examples = sum(len(rec["examples"]) for rec in done.values())

    def rate(items: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(items)
        p = sum(1 for c in items if c["status"] == "passed")
        lo, hi = wilson(p, n)
        return {"n": n, "passed": p, "rate": round(p / n, 4) if n else 0.0, "ci95": [lo, hi]}

    by_pack: dict[str, list] = {}
    by_kind: dict[str, list] = {}
    for c in cases:
        by_kind.setdefault(c.get("kind") or "?", []).append(c)
    for rec in done.values():
        by_pack.setdefault(rec["pack"], []).extend(rec["cases"])
    failed = Counter(f for c in cases for f in c.get("failed") or [])
    splits = Counter(c["tool_split"] for c in cases if c.get("tool_split"))
    secs = sorted(float((c.get("timing") or {}).get("seconds") or 0) for c in cases)
    return {
        "shards": len(done), "cases": len(cases), "usd": round(usd, 4),
        "usd_per_case": round(usd / len(cases), 6) if cases else None,
        "examples": examples,
        "usd_per_example": round(usd / examples, 6) if examples else None,
        "all": rate(cases),
        "by_pack": {k: rate(v) for k, v in sorted(by_pack.items())},
        "by_kind": {k: rate(v) for k, v in sorted(by_kind.items())},
        "by_source": {k: rate([c for c in cases if c.get("source") == k])
                      for k in sorted({c.get("source") for c in cases})},
        "failed_scorers": dict(failed.most_common()),
        "tool_split": dict(splits.most_common()),
        "median_s": secs[len(secs) // 2] if secs else None,
        "p95_s": secs[int(len(secs) * 0.95)] if secs else None,
    }  # fmt: skip


def failed_by_all(states: list[Path]) -> set[str]:
    """Case ids (`pack/case_id`) that did not pass in any of these eval states."""
    sets = []
    for state in states:
        bad = set()
        for rec in load_done(state).values():
            bad |= {
                f"{rec['pack']}/{c['case_id']}" for c in rec["cases"] if c["status"] != "passed"
            }
        sets.append(bad)
    return set.intersection(*sets) if sets else set()


# -- the dataset -------------------------------------------------------------------------------


def _users(example: dict[str, Any]) -> list[str]:
    return [
        m.get("content") or "" for m in example.get("messages") or [] if m.get("role") == "user"
    ]


def finalize(
    tuning: Tuning,
    state: Path,
    name: str,
    *,
    tenant: str = "dev",
    exclude_prompts: set[str] = frozenset(),
    exclude_ids: set[str] = frozenset(),
    val_fraction: float = 0.1,
    val_max: int = 160,
    max_per_prompt: int = 3,
    sizes: list[int] | None = None,
) -> dict[str, Any]:
    """The kept examples of a curate state as `dataset/<name>` (and `dataset/<name>_<size>`
    for each size, nested: a smaller set is a prefix of a larger one in hash order, all with
    the same validation set). Filters, in order: an example whose user message is an excluded
    prompt (golden or test) or that mentions a test entity id is dropped; identical examples
    are deduplicated by content hash; at most `max_per_prompt` examples share a first user
    message. The validation share is chosen by hash and capped at `val_max`."""
    done = load_done(state)
    plan_data = json.loads((state / "plan.json").read_text(encoding="utf-8"))
    dropped: Counter = Counter()
    examples = []
    for rec in sorted(done.values(), key=lambda r: r["shard"]):
        for ex in rec["examples"]:
            users = [cur.prompts_key(u) for u in _users(ex)]
            if any(u in exclude_prompts for u in users):
                dropped["excluded_prompt"] += 1
                continue
            if exclude_ids and set(gen.ID_PATTERN.findall(json.dumps(ex))) & exclude_ids:
                dropped["test_entity"] += 1
                continue
            examples.append(ex)
    unique: dict[str, dict[str, Any]] = {}
    for ex in examples:
        unique.setdefault(cur.content_hash(ex), ex)
    dropped["duplicate"] = len(examples) - len(unique)
    per_prompt: Counter = Counter()
    ordered = []
    for digest in sorted(unique):
        ex = unique[digest]
        first = cur.prompts_key((_users(ex) or [""])[0])
        per_prompt[first] += 1
        if per_prompt[first] > max_per_prompt:
            dropped["per_prompt_cap"] += 1
            continue
        ordered.append((digest, ex))
    val, train = [], []
    for digest, ex in ordered:
        row = {"id": digest[:16], **ex}
        (val if cur.is_val(digest, val_fraction) and len(val) < val_max else train).append(row)
    sizes = sorted({int(s) for s in sizes or [] if 0 < int(s) < len(train)})
    out = {}
    for size in [*sizes, None]:
        part = train[:size] if size else train
        out[f"{name}_{size}" if size else name] = _put_dataset(
            tuning, tenant, f"{name}_{size}" if size else name, part, val, plan_data, done,
            dict(dropped), size,
        )  # fmt: skip
    return out


def _put_dataset(
    tuning: Tuning,
    tenant: str,
    name: str,
    train: list,
    val: list,
    plan_data: dict,
    done: dict,
    dropped: dict,
    size: int | None,
) -> dict[str, Any]:
    from harnesswalk.events import iso
    from harnesswalk.ops.tune import _NoVersion, _rel

    version = (tuning.artifacts.get("", "dataset", name) or _NoVersion()).version + 1
    folder = tuning.dir / "datasets" / name / f"v{version}"
    files = {"train": cur.write_jsonl(folder / "train.jsonl", train),
             "val": cur.write_jsonl(folder / "val.jsonl", val)}  # fmt: skip
    teacher = plan_data["model"]
    teacher_cfg = tuning.h.settings.get(f"models.models.{teacher}", {}) or {}
    all_rows = train + val
    manifest = {
        "kind": "dataset", "name": name, "created_at": iso(time.time()),
        "teacher": teacher, "teacher_model": teacher_cfg.get("name", teacher),
        "teachers": dict(Counter(r["meta"]["teacher"] for r in all_rows)),
        "packs": sorted({r["meta"]["pack"] for r in all_rows}),
        "golden_included": False,
        "scenarios": {"generated": plan_data["n_scenarios"], "variants": plan_data["variants"],
                      "seed": plan_data["seed"], "hash": plan_data["scenarios"]},
        "filters": [
            "every deterministic scorer passed under model grading (the model suite)",
            "the turn ended on an answer and the trace kept its content",
            "no user message is a golden prompt of any pack or a test-set prompt",
            "no test-set entity id appears in the example",
            "deduplicated by the sha256 of the example",
            "at most 3 examples share a first user message",
            "validation: the examples whose hash is lowest (a tenth), at most 160",
            *([f"subset: the first {size} training examples in hash order"] if size else []),
        ],
        "counts": {
            "train": len(train), "val": len(val), "shards": len(done),
            "cases": sum(len(r["cases"]) for r in done.values()),
            "kept_cases": sum(1 for r in done.values() for c in r["cases"]
                              if c["status"] == "passed"),
            "dropped": dropped,
            "by_pack": dict(Counter(r["meta"]["pack"] for r in train)),
            "by_kind": dict(Counter(r["meta"].get("kind") for r in train)),
            "by_source": dict(Counter(r["meta"].get("source") for r in train)),
            "by_teacher": dict(Counter(r["meta"]["teacher"] for r in train)),
        },
        "usd": round(sum(r["usd"] for r in done.values()), 4),
        "files": {k: {**v, "path": _rel(Path(v["path"]), tuning.h.settings.root)}
                  for k, v in files.items()},
        "synthetic": True, "source": cur.SOURCE,
    }  # fmt: skip
    ref = tuning.put_artifact(tenant, "dataset", name, manifest, "P6 scaled curation")
    return {"ref": ref, "counts": manifest["counts"]}
