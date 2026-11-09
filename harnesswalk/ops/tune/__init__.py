"""Tuning: curate, tune, compare, promote, canary and rollback. spec/contracts/tuning.md.

Node: `tune` (expert child of `release`, kind code); `curate` (child of `eval`) is curate.py.

    traces + model-suite results -> curate -> dataset/<name> -> tune (a trainer)
      -> adapter/<name> + model/<name> -> compare (beat the base, no forgetting, beside the
      prompt arm) -> promote (router.canary, then the whole pack) -> rollback

Every step is an explicit request (CLI or HTTP); one runs at a time per harness. Curations,
runs and comparisons are background tasks whose progress is on ops streams (`curate:<id>`,
`tune:<id>`, and each comparison's eval runs); their records are in `tune.sqlite_path`. Promote
and rollback change what is served only through a `models/models` edit and a release per pack.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from harnesswalk.events import iso, new_trace_id
from harnesswalk.nodes import register
from harnesswalk.ops.artifacts import SKILL_NAME, TUNE_KINDS, sha256, unified_diff
from harnesswalk.ops.tune import curate as cur
from harnesswalk.ops.tune.models_edit import canary_of, with_alias, with_canary
from harnesswalk.ops.tune.store import TuneStore
from harnesswalk.ops.tune.trainers import (
    TrainerError,
    TrainJob,
    build_trainer,
    total_steps,
    tuned_alias,
)

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

register("tune", __name__)
log = logging.getLogger("harnesswalk.tune")

ARM_JOIN = "\n\n"  # how the control arm's addition is appended to the system prompt
GATE_DEFAULTS = {"samples": 2, "min_gain_cases": 2, "canary_tolerance_cases": 1}
CANARY_DEFAULTS = {"fraction": 0.1, "min_turns": 5, "max_failure_increase": 0.1}


class TuneError(Exception):
    """A request the tuning loop refuses. Maps to an HTTP status and code."""

    def __init__(self, status: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.detail = status, code, message, detail


def ref_text(ref: dict[str, Any] | None, kind: str) -> str | None:
    return f"{kind}/{ref['name']}@v{ref['version']}" if ref else None


def parse_ref(value: str, kind: str) -> tuple[str, int | None]:
    """`model/<name>`, `model/<name>@v3`, `<name>` or `<name>@v3` -> (name, version)."""
    text = value.strip()
    if text.startswith(f"{kind}/"):
        text = text[len(kind) + 1 :]
    name, _, version = text.partition("@")
    if not SKILL_NAME.match(name):
        raise TuneError(400, "invalid_request", f"{kind} name {name!r} is not valid")
    if version:
        if not version.lstrip("v").isdigit():
            raise TuneError(400, "invalid_request", f"bad version in {value!r}")
        return name, int(version.lstrip("v"))
    return name, None


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


class Tuning:
    def __init__(self, harness: Harness) -> None:
        self.h = harness
        s = harness.settings
        self.cfg: dict[str, Any] = s.get("tune", {}) or {}
        self.store = TuneStore(str(s.resolve(self.cfg.get("sqlite_path", "data/tune.db"))))
        self.dir = s.resolve(self.cfg.get("artifacts_dir", "data/tune"))
        self.task: asyncio.Task | None = None
        self._turn_packs: dict[str, str] = {}
        self._canary_turns: set[str] = set()  # turns the canary rule routed, until turn_end
        self._unsubscribe = [harness.bus.subscribe(self._on_turn_event)]
        self._unsubscribe.append(harness.ops.subscribe(self._on_ops_event))

    # -- configuration -----------------------------------------------------------------------------

    @property
    def artifacts(self) -> Any:
        return self.h.release.store

    def gate_cfg(self) -> dict[str, Any]:
        return {**GATE_DEFAULTS, **(self.cfg.get("gate") or {})}

    def canary_cfg(self) -> dict[str, Any]:
        return {**CANARY_DEFAULTS, **(self.cfg.get("canary") or {})}

    def trainers(self) -> dict[str, dict[str, Any]]:
        return dict(self.cfg.get("trainers") or {})

    def students(self) -> dict[str, dict[str, Any]]:
        return dict(self.cfg.get("students") or {})

    def prompt_arm_text(self) -> str:
        rel = self.cfg.get("prompt_arm", "config/tune/prompt_arm.md")
        path = self.h.settings.root / rel
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def overview(self, tenant_id: str) -> dict[str, Any]:
        trainers = []
        for alias, cfg in self.trainers().items():
            trainers.append({
                "alias": alias, "kind": cfg.get("kind"), "implemented": cfg.get("kind") != "stub",
                "note": cfg.get("note"),
            })  # fmt: skip
        return {
            "trainers": trainers,
            "students": sorted(self.students()),
            "teacher": self.cfg.get("teacher", "local-27b"),
            "gate": self.gate_cfg(),
            "canary": self.canary(tenant_id),
            "prompt_arm": self.prompt_arm_text(),
            "running": self.running(),
            "replay": False,
        }

    def running(self) -> dict[str, Any] | None:
        if self.task is None or self.task.done():
            return None
        return {"id": self.task.get_name()}

    # -- artifacts ---------------------------------------------------------------------------------

    def put_artifact(
        self, tenant_id: str, kind: str, name: str, manifest: dict[str, Any], note: str
    ) -> dict[str, Any]:
        from harnesswalk.ops.ops_events import OPS_STREAM

        body = canonical_pretty(manifest)
        new, previous = self.artifacts.put("", kind, name, body, by=f"user:{tenant_id}", note=note)
        summary: dict[str, Any] = {"kind": kind, "name": name, "version": new.version,
                                   "hash": new.hash}  # fmt: skip
        if previous is not None and previous.version != new.version:
            summary["previous_version"] = previous.version
        self.h.ops.emit(tenant_id, OPS_STREAM, "artifact_changed", "release", "done", summary)
        return {"name": name, "version": new.version, "hash": new.hash}

    def get_artifact(self, kind: str, name: str, version: int | None = None) -> dict[str, Any]:
        if kind not in TUNE_KINDS:
            raise TuneError(404, "artifact_not_found", f"no tune artifact kind {kind!r}")
        v = self.artifacts.get("", kind, name, version)
        if v is None:
            raise TuneError(404, "artifact_not_found", f"no {kind}/{name}")
        history = self.artifacts.history("", kind, name)
        prev = next((h for h in history if h.version == v.version - 1), None)
        out = v.meta()
        out["manifest"] = yaml.safe_load(v.body)
        out["history"] = [h.meta() for h in history]
        out["diff_from_previous"] = (
            unified_diff(prev.body, v.body, f"{kind}/{name}.json") if prev else ""
        )
        return out

    def manifest(self, kind: str, name: str, version: int | None = None) -> tuple[dict, dict]:
        """(manifest, ref) of a tune artifact version."""
        v = self.artifacts.get("", kind, name, version)
        if v is None:
            raise TuneError(404, "artifact_not_found", f"no {kind}/{name}")
        return yaml.safe_load(v.body), {"name": name, "version": v.version, "hash": v.hash}

    def list_artifacts(self, kind: str | None = None) -> list[dict[str, Any]]:
        out = []
        for v in self.artifacts.tune_latest(kind):
            item = v.meta()
            data = yaml.safe_load(v.body) or {}
            item["summary"] = summarize_manifest(v.kind, data)
            out.append(item)
        return out

    def verify(self) -> dict[str, Any]:
        """Every reference a tune manifest makes names a version that exists with that hash."""
        problems = []
        checked = 0
        for kind in TUNE_KINDS:
            for latest in self.artifacts.tune_latest(kind):
                for v in self.artifacts.history("", kind, latest.name):
                    checked += 1
                    if sha256(v.body) != v.hash:
                        problems.append(f"{kind}/{v.name}@v{v.version}: body does not hash")
                    data = yaml.safe_load(v.body) or {}
                    for parent in ("dataset", "adapter"):
                        ref = data.get(parent) if isinstance(data.get(parent), dict) else None
                        if not ref or parent == kind:
                            continue
                        got = self.artifacts.get("", parent, ref["name"], ref["version"])
                        if got is None or got.hash != ref["hash"]:
                            problems.append(
                                f"{kind}/{v.name}@v{v.version}: {parent}/{ref['name']}"
                                f"@v{ref['version']} is missing or changed"
                            )
        return {"ok": not problems, "versions": checked, "problems": problems[:20]}

    # -- jobs --------------------------------------------------------------------------------------

    def _start(self, kind: str, tenant_id: str, request: dict[str, Any], coro_fn: Any) -> dict:
        if self.h.settings.mode == "public":
            raise TuneError(403, "forbidden_in_mode", "the public sandbox never tunes")
        if self.running():
            raise TuneError(409, "tune_running", "a curation, run or comparison is running")
        job_id = self.store.new_id(kind)
        record = {
            "id": job_id, "kind": kind, "tenant_id": tenant_id, "status": "running",
            "created_at": iso(time.time()), "ended_at": None, "request": request,
            "trace_id": new_trace_id(), "error": None,
            "stream": {"curation": f"curate:{job_id}", "run": f"tune:{job_id}"}.get(kind, "ops"),
        }  # fmt: skip
        self.store.put(kind, record)

        async def go() -> None:
            try:
                await coro_fn(record)
                record["status"] = "done"
            except asyncio.CancelledError:
                record["status"], record["error"] = "error", {"code": "cancelled", "message": ""}
                raise
            except Exception as exc:  # a job that fails is a result, recorded with its reason
                log.warning("tune %s %s failed: %s", kind, job_id, exc)
                record["status"] = "error"
                record["error"] = {
                    "code": getattr(exc, "code", "internal"),
                    "message": f"{exc}"[:500],
                }
            finally:
                record["ended_at"] = iso(time.time())
                self.store.put(kind, record)

        self.task = asyncio.get_running_loop().create_task(go(), name=job_id)
        return record

    async def wait(self) -> None:
        if self.task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    def job(self, kind: str, tenant_id: str, job_id: str) -> dict[str, Any]:
        record = self.store.get(job_id, tenant_id)
        if record is None or record.get("kind") != kind:
            raise TuneError(404, "not_found", f"no {kind} {job_id}")
        if record.get("stream") and record["stream"] != "ops":  # what its stream holds, by type
            events = self.h.ops.list(tenant_id, record["stream"], after=-1, limit=100000)
            record["event_counts"] = dict(Counter(e["type"] for e in events))
        return record

    def recover(self) -> int:
        n = 0
        for record in self.store.unfinished():
            if self.task is not None and self.task.get_name() == record["id"]:
                continue
            record["status"] = "error"
            record["ended_at"] = iso(time.time())
            record["error"] = {"code": "restarted", "message": "the harness stopped during it"}
            self.store.put(record["kind"], record)
            n += 1
        return n

    def _emit(self, record: dict[str, Any], type_: str, node: str, status: str, summary: dict):
        return self.h.ops.emit(
            record["tenant_id"], record["stream"], type_, node, status,
            {k: v for k, v in summary.items() if v is not None}, trace_id=record["trace_id"],
        )  # fmt: skip

    # -- views -------------------------------------------------------------------------------------

    def _models_text(self) -> str:
        v = self.h.release.store.get(self.h.pack, "models", "models")
        if v is None:
            raise TuneError(422, "invalid_request", "no models/models artifact")
        return v.body

    def _prompt_text(self, pack: str) -> str | None:
        view = self.h.view(pack)
        v = view.release.store.get(pack, "prompt", "system_prompt")
        return v.body if v is not None else None

    def _at_temperature_zero(self, text: str, aliases: list[str], extra: dict | None = None):
        """models.yaml text with these aliases at temperature 0 where the provider takes it
        (Ollama), and `extra` aliases added: what a comparison's arms are served."""
        data = yaml.safe_load(text) or {}
        models = dict(data.get("models") or {})
        for alias, cfg in (extra or {}).items():
            models[alias] = cfg
            text = with_alias(text, alias, cfg, f"{alias}: a tuning candidate (not promoted)")
        for alias in aliases:
            cfg = dict(models.get(alias) or {})
            if cfg.get("provider") != "ollama":
                continue
            params = dict(cfg.get("params") or {})
            params["options"] = {**(params.get("options") or {}), "temperature": 0}
            cfg["params"] = params
            text = with_alias(text, alias, cfg, f"{alias} at temperature 0 for a comparison")
        return text

    def _view(self, pack: str, changes: dict[tuple[str, str], str]) -> Any:
        try:
            return self.h.candidate_view(pack, changes)
        except KeyError as exc:
            raise TuneError(422, "invalid_request", f"no dataset pack {pack!r}") from exc

    async def _model_run(
        self, tenant_id: str, pack: str, alias: str, view: Any, purpose: str, cases: Any = None
    ) -> dict[str, Any]:
        from harnesswalk.ops.eval import EvalRequestError

        offline = self.h.evals.offline
        try:
            run = offline.start(
                tenant_id, pack, "model", model=alias, by=f"tune:{purpose}", purpose=purpose,
                runtime=view, cases=cases,
            )  # fmt: skip
        except EvalRequestError as exc:
            raise TuneError(exc.status, exc.code, exc.message) from exc
        await offline.wait(run["run_id"])
        return self.h.evals.store.run(tenant_id, run["run_id"]) or run

    # -- curate ------------------------------------------------------------------------------------

    def start_curation(self, tenant_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").removeprefix("dataset/")
        if not SKILL_NAME.match(name):
            raise TuneError(400, "invalid_request", "name must match ^[a-z][a-z0-9_]{0,63}$")
        packs = [str(p) for p in body.get("packs") or []]
        if not packs or not all(self.h.pack_known(p) for p in packs):
            raise TuneError(422, "invalid_request", f"unknown or missing packs {packs}")
        teacher = str(body.get("teacher") or self.cfg.get("teacher", "local-27b"))
        cfg = self.h.settings.get(f"models.models.{teacher}", None)
        if not cfg or cfg.get("embedding"):
            raise TuneError(400, "invalid_request", f"unknown teacher alias {teacher!r}")
        ccfg = self.cfg.get("curate") or {}
        request = {
            "name": name,
            "packs": packs,
            "teacher": teacher,
            "variants": int(body.get("variants", ccfg.get("variants", 6))),
            "seed": int(body.get("seed", ccfg.get("seed", 7))),
            "val_fraction": float(body.get("val_fraction", ccfg.get("val_fraction", 0.1))),
            "teacher_prompt_arm": bool(
                body.get("teacher_prompt_arm", ccfg.get("teacher_prompt_arm", True))
            ),
            "chunk": int(ccfg.get("chunk", 24)),
        }
        if not 0 <= request["variants"] <= 16:
            raise TuneError(400, "invalid_request", "variants must be from 0 to 16")
        return self._start("curation", tenant_id, request, self._curate)

    async def _curate(self, record: dict[str, Any]) -> None:
        req = record["request"]
        tenant = record["tenant_id"]
        teacher = req["teacher"]
        arm = self.prompt_arm_text() if req["teacher_prompt_arm"] else ""
        offline = self.h.evals.offline
        per_pack: dict[str, list[Any]] = {}
        rewrite_usage = Counter()
        skipped_multi = 0
        for pack in req["packs"]:
            cases = offline.golden(pack)
            texts: dict[str, list[str]] = {}
            for case in cases:
                if case.multi_turn:
                    skipped_multi += 1
                    continue
                got, usage = await cur.rewrites(self, teacher, case, req["variants"], req["seed"])
                texts[case.id] = got
                rewrite_usage.update(usage)
            per_pack[pack] = cur.scenarios_for(cases, texts)
        total = sum(len(v) for v in per_pack.values())
        record["scenarios"] = {p: len(v) for p, v in per_pack.items()}
        self._emit(record, "eval_run_started", "curate", "active", {
            "run_id": record["id"], "purpose": "curate", "suite": "model", "model": teacher,
            "pack": req["packs"][0], "packs": req["packs"], "total": total,
        })  # fmt: skip
        examples: list[dict[str, Any]] = []
        dropped: Counter = Counter()
        index = passed = failed = skipped = 0
        run_ids: list[str] = []
        scenario_rows: list[dict[str, Any]] = []
        models_text = self._at_temperature_zero(self._models_text(), [teacher])
        for pack, cases in per_pack.items():
            changes: dict[tuple[str, str], str] = {("models", "models"): models_text}
            base_prompt = self._prompt_text(pack)
            if arm and base_prompt is not None:
                changes[("prompt", "system_prompt")] = base_prompt.rstrip() + ARM_JOIN + arm
            view = self._view(pack, changes)
            by_id = {c.id: c for c in cases}
            originals = {c.id for c in offline.golden(pack)}
            try:
                for chunk in cur.chunks(cases, int(req.get("chunk") or 24)):
                    run = await self._model_run(tenant, pack, teacher, view, "curate", chunk)
                    run_ids.append(run["run_id"])
                    case_tenant = run.get("case_tenant") or f"evalrun-{run['run_id']}"
                    for c in self.h.evals.store.cases(run["run_id"]):
                        index += 1
                        case = by_id.get(c["case_id"])
                        source = "golden" if c["case_id"] in originals else "paraphrase"
                        scenario_rows.append({
                            "pack": pack, "case_id": c["case_id"], "source": source,
                            "prompt": case.prompt if case else None, "status": c["status"],
                        })  # fmt: skip
                        kept = c["status"] == "passed"
                        if kept:
                            passed += 1
                            for turn_id in c.get("turn_ids") or [c.get("turn_id")]:
                                ex = (
                                    cur.example_of(self, case_tenant, turn_id, view, arm)
                                    if turn_id
                                    else None
                                )
                                if ex is None:
                                    dropped["no_content"] += 1
                                    continue
                                ex["meta"] = {"pack": pack, "case_id": c["case_id"],
                                              "source": source, "teacher": teacher}  # fmt: skip
                                examples.append(ex)
                        elif c["status"] == "skipped":
                            skipped += 1
                        else:
                            failed += 1
                            scores = c.get("scores") or {}
                            bad = [k for k, v in scores.items() if not v.get("pass")]
                            dropped[bad[0] if bad else c["status"]] += 1
                        self._emit(record, "eval_case_finished", "curate", "done", {
                            "run_id": record["id"], "case_id": f"{pack}/{c['case_id']}",
                            "status": c["status"], "kept": kept, "index": index, "total": total,
                        })  # fmt: skip
            finally:
                view.close()
        unique: dict[str, dict[str, Any]] = {}
        for ex in examples:
            unique.setdefault(cur.content_hash(ex), ex)
        train, val = [], []
        for digest in sorted(unique):
            ex = {"id": digest[:16], **unique[digest]}
            (val if cur.is_val(digest, req["val_fraction"]) else train).append(ex)
        if not val and len(train) > 1:  # a tiny set still gets a validation example
            val.append(train.pop())
        version = (self.artifacts.get("", "dataset", req["name"]) or _NoVersion()).version + 1
        folder = self.dir / "datasets" / req["name"] / f"v{version}"
        files = {
            "train": cur.write_jsonl(folder / "train.jsonl", train),
            "val": cur.write_jsonl(folder / "val.jsonl", val),
            "scenarios": cur.write_jsonl(folder / "scenarios.jsonl", scenario_rows),
        }
        by_pack = Counter(ex["meta"]["pack"] for ex in unique.values())
        teacher_cfg = self.h.settings.get(f"models.models.{teacher}", {})
        manifest = {
            "kind": "dataset",
            "name": req["name"],
            "created_at": iso(time.time()),
            "curation_id": record["id"],
            "teacher": teacher,
            "teacher_model": teacher_cfg.get("name", teacher),
            "teacher_prompt_arm": req["teacher_prompt_arm"],
            "packs": req["packs"],
            "scenarios": {
                "golden": sum(1 for r in scenario_rows if r["source"] == "golden"),
                "paraphrase": sum(1 for r in scenario_rows if r["source"] == "paraphrase"),
                "variants": req["variants"],
                "seed": req["seed"],
                "multi_turn_not_rewritten": skipped_multi,
                "rewrite_tokens": dict(rewrite_usage),
            },
            "filters": [
                "every deterministic scorer passed under model grading (the model suite)",
                "the turn ended on an answer and the trace kept its content",
                "deduplicated by the sha256 of the example",
                f"validation split: the {req['val_fraction']} of examples whose hash is lowest",
            ],
            "counts": {
                "turns": index,
                "kept": passed,
                "failed": failed,
                "skipped": skipped,
                "dropped": dict(dropped),
                "examples": len(examples),
                "deduplicated": len(examples) - len(unique),
                "train": len(train),
                "val": len(val),
                "by_pack": dict(by_pack),
            },
            "files": {
                k: {**v, "path": _rel(Path(v["path"]), self.h.settings.root)}
                for k, v in files.items()
            },  # fmt: skip
            "eval_run_ids": run_ids,
            "synthetic": True,
            "source": cur.SOURCE,
            "regenerate": (
                f"harnesswalk tune curate {' '.join('--pack ' + p for p in req['packs'])}"
                f" --teacher {teacher} --variants {req['variants']} --seed {req['seed']}"
                f"{'' if req['teacher_prompt_arm'] else ' --no-teacher-prompt-arm'}"
                f" --out dataset/{req['name']}"
            ),
        }
        ref = self.put_artifact(
            tenant, "dataset", req["name"], manifest, f"curated by {record['id']}"
        )
        record["dataset"] = ref
        record["counts"] = manifest["counts"]
        self._emit(record, "eval_run_finished", "curate", "done", {
            "run_id": record["id"], "status": "done",
            "totals": {"cases": index, "passed": passed, "failed": failed, "skipped": skipped},
            "kept": len(unique), "dataset": ref_text(ref, "dataset"),
        })  # fmt: skip

    # -- tune runs ---------------------------------------------------------------------------------

    def start_run(self, tenant_id: str, body: dict[str, Any]) -> dict[str, Any]:
        dataset_name, dataset_version = parse_ref(str(body.get("dataset") or ""), "dataset")
        manifest, dref = self.manifest("dataset", dataset_name, dataset_version)
        trainer = str(body.get("trainer") or "fake")
        tcfg = self.trainers().get(trainer)
        if tcfg is None:
            raise TuneError(400, "invalid_request", f"unknown trainer {trainer!r}")
        if tcfg.get("kind") == "stub":
            raise TuneError(422, "trainer_unavailable", f"trainer {trainer} is not implemented")
        student = str(body.get("student") or "")
        scfg = self.h.settings.get(f"models.models.{student}", None)
        if not scfg or scfg.get("embedding"):
            raise TuneError(400, "invalid_request", f"unknown student alias {student!r}")
        if tcfg.get("kind") == "ssh" and student not in self.students():
            raise TuneError(422, "invalid_request", f"trainers.yaml has no student {student}")
        name = str(body.get("name") or f"{dataset_name}_{student.replace('-', '')}")[:64]
        if not SKILL_NAME.match(name):
            raise TuneError(400, "invalid_request", "name must match ^[a-z][a-z0-9_]{0,63}$")
        config = body.get("config") or {}
        if not isinstance(config, dict):
            raise TuneError(400, "invalid_request", "config must be an object")
        request = {
            "dataset": dref, "trainer": trainer, "student": student, "name": name,
            "config": config, "dataset_manifest_counts": manifest.get("counts"),
        }  # fmt: skip
        return self._start("run", tenant_id, request, self._run)

    async def _run(self, record: dict[str, Any]) -> None:
        req = record["request"]
        tenant = record["tenant_id"]
        manifest, dref = self.manifest("dataset", req["dataset"]["name"], req["dataset"]["version"])
        root = self.h.settings.root
        files = {k: _abs(manifest["files"][k]["path"], root) for k in ("train", "val")}
        for key, path in files.items():
            if not path.is_file():
                raise TrainerError("dataset_missing", f"the {key} file {path} is not on disk")
        tcfg = self.trainers()[req["trainer"]]
        trainer = build_trainer(req["trainer"], tcfg, root)
        job = TrainJob(
            run_id=record["id"], name=req["name"], trainer=req["trainer"],
            student=req["student"],
            student_cfg=self.h.settings.get(f"models.models.{req['student']}", {}),
            base=self.students().get(req["student"], {}), dataset=manifest, dataset_ref=dref,
            files=files, config=req["config"], artifacts_dir=self.dir,
        )  # fmt: skip
        from harnesswalk.ops.tune.trainers import DEFAULT_CONFIG

        planned = {**DEFAULT_CONFIG, **(tcfg.get("defaults") or {}), **req["config"]}
        steps = total_steps(int((manifest.get("counts") or {}).get("train") or 1), planned)
        self._emit(record, "tune_run_started", "tune", "active", {
            "run_id": record["id"], "trainer": req["trainer"], "trainer_kind": trainer.kind,
            "student": req["student"], "teacher": manifest.get("teacher"),
            "dataset": ref_text(dref, "dataset"),
            "examples": {"train": manifest["counts"]["train"], "val": manifest["counts"]["val"]},
            "total_steps": steps,
            "config": {k: planned.get(k) for k in ("method", "rank", "epochs", "learning_rate",
                                                  "max_seq_len")},
        })  # fmt: skip
        record["steps"] = []

        async def on_step(event: dict[str, Any]) -> None:
            step = int(event.get("step") or 0)
            if step < 1:
                return
            summary = {"run_id": record["id"], **{k: event.get(k) for k in (
                "step", "total_steps", "epoch", "loss", "eval_loss", "learning_rate",
                "elapsed_s", "eta_s", "phase")}}  # fmt: skip
            self._emit(record, "tune_step", "tune", "active", summary)
            record["steps"].append({k: v for k, v in summary.items() if v is not None})
            record["last_step"] = record["steps"][-1]
            if len(record["steps"]) % 10 == 0:
                self.store.put("run", record)

        try:
            result = await trainer.train(job, on_step)
        except TrainerError as exc:
            self._emit(record, "tune_run_finished", "tune", "error", {
                "run_id": record["id"], "status": "error", "error_code": exc.code,
            })  # fmt: skip
            raise
        adapter = {
            "kind": "adapter", "name": req["name"], "created_at": iso(time.time()),
            "run_id": record["id"], "dataset": dref, "trainer": req["trainer"],
            "trainer_kind": trainer.kind, "student": req["student"],
            "base_model": result.base_model, "config": result.config,
            "metrics": result.metrics, "weights": result.weights,
        }  # fmt: skip
        aref = self.put_artifact(
            tenant, "adapter", req["name"], adapter, f"trained by {record['id']}"
        )
        alias = tuned_alias(req["name"])
        model = {
            "kind": "model", "name": req["name"], "created_at": iso(time.time()),
            "run_id": record["id"], "adapter": aref, "alias": alias,
            "alias_config": result.alias_config, "serve": result.serve,
            "modelfile": result.modelfile, "student": req["student"], "gate": None,
        }  # fmt: skip
        mref = self.put_artifact(
            tenant, "model", req["name"], model, f"served model of {record['id']}"
        )
        record["adapter"], record["model"], record["metrics"] = aref, mref, result.metrics
        self._emit(record, "tune_run_finished", "tune", "done", {
            "run_id": record["id"], "status": "done", "adapter": ref_text(aref, "adapter"),
            "model": ref_text(mref, "model"), "metrics": result.metrics,
        })  # fmt: skip

    # -- compare: the model gate -------------------------------------------------------------------

    def start_comparison(self, tenant_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name, version = parse_ref(str(body.get("candidate") or ""), "model")
        _, mref = self.manifest("model", name, version)
        against = str(body.get("against") or "")
        cfg = self.h.settings.get(f"models.models.{against}", None)
        if not cfg or cfg.get("embedding"):
            raise TuneError(400, "invalid_request", f"unknown base alias {against!r}")
        packs = _packs(body.get("packs"))
        canary = _packs(body.get("canary_packs"))
        for p in [*packs, *canary]:
            if not self.h.pack_known(p):
                raise TuneError(422, "invalid_request", f"no dataset pack {p!r}")
        if not packs:
            raise TuneError(400, "invalid_request", "packs (the target) must name a pack")
        gate = self.gate_cfg()
        request = {
            "candidate": mref, "against": against, "packs": packs, "canary_packs": canary,
            "samples": max(1, int(body.get("samples") or gate["samples"])),
            "prompt_arm": bool(body.get("prompt_arm", True)),
            "min_gain_cases": float(gate["min_gain_cases"]),
            "canary_tolerance_cases": float(gate["canary_tolerance_cases"]),
        }  # fmt: skip
        return self._start("comparison", tenant_id, request, self._compare)

    async def _compare(self, record: dict[str, Any]) -> None:
        from harnesswalk.ops.eval import scorecard as sc
        from harnesswalk.ops.eval.offline import read_tools_of

        req = record["request"]
        tenant = record["tenant_id"]
        model, mref = self.manifest("model", req["candidate"]["name"], req["candidate"]["version"])
        alias, against = model["alias"], req["against"]
        base_models = self._at_temperature_zero(self._models_text(), [against])
        cand_models = self._at_temperature_zero(
            self._models_text(), [against, alias], {alias: model["alias_config"]}
        )
        arm_text = self.prompt_arm_text()
        arms = [("base", against), ("candidate", alias)]
        if req["prompt_arm"] and arm_text:
            arms.append(("prompt_arm", against))
        cells: dict[str, dict[str, Any]] = {a: {} for a, _ in arms}
        run_ids: list[str] = []
        for pack in [*req["packs"], *req["canary_packs"]]:
            for arm, arm_alias in arms:
                changes = {("models", "models"): cand_models if arm == "candidate" else base_models}
                if arm == "prompt_arm":
                    prompt = self._prompt_text(pack)
                    if prompt is not None:
                        changes[("prompt", "system_prompt")] = prompt.rstrip() + ARM_JOIN + arm_text
                view = self._view(pack, changes)
                samples = []
                try:
                    read_tools = read_tools_of(view)
                    for _ in range(req["samples"]):
                        run = await self._model_run(tenant, pack, arm_alias, view, "tune_gate")
                        run_ids.append(run["run_id"])
                        cases = self.h.evals.store.cases(run["run_id"])
                        samples.append(sc.sample_of(run, cases, read_tools))
                finally:
                    view.close()
                cells[arm][pack] = cell_summary(samples)
                record["cells"] = cells
                self.store.put("comparison", record)
        verdict = gate_verdict(cells, req)
        verdict.update({"comparison_id": record["id"], "against": against, "run_ids": run_ids,
                        "packs": req["packs"], "canary_packs": req["canary_packs"],
                        "samples": req["samples"], "at": iso(time.time())})  # fmt: skip
        record["verdict"] = verdict
        model["gate"] = verdict
        gated = self.put_artifact(
            tenant, "model", model["name"], model,
            f"gate {verdict['status']} by {record['id']}",
        )  # fmt: skip
        record["model"] = gated
        from harnesswalk.ops.ops_events import OPS_STREAM

        summary = {
            "pack": req["packs"][0], "gate": verdict["status"], "suite": "model",
            "reason": verdict.get("reason"), "run_ids": run_ids[:20], "packs": req["packs"],
            "canary_packs": req["canary_packs"], "candidate": ref_text(gated, "model"),
            "gain": verdict["gain"], "canary_drop": verdict["canary_drop"],
            "prompt_arm": verdict.get("prompt_arm"),
        }  # fmt: skip
        self.h.ops.emit(
            tenant, OPS_STREAM, "release_gated", "release",
            "error" if verdict["status"] == "failed" else "done",
            {k: v for k, v in summary.items() if v is not None},
        )  # fmt: skip

    # -- promote, canary, rollback -----------------------------------------------------------------

    def canary(self, tenant_id: str | None) -> dict[str, Any]:
        """The canary rule as served, the last setting, both arms' counts and the gate."""
        try:
            rule = canary_of(self._models_text())
        except TuneError:
            rule = None
        last = self.store.canary_last()
        since = float(last["at_s"]) if last else 0.0
        rows = self.store.canary_rows(since, tenant_id)
        arms = {}
        for arm in ("base", "candidate"):
            mine = [r for r in rows if r["arm"] == arm]
            failed = [
                r for r in mine
                if r["bad"] or r["status"] == "error" or r["exit"] in ("error", "limit")
            ]  # fmt: skip
            arms[arm] = {
                "turns": len(mine),
                "failed": len(failed),
                "bad_feedback": sum(1 for r in mine if r["bad"]),
                "failure_rate": round(len(failed) / len(mine), 4) if mine else None,
            }
        cfg = self.canary_cfg()
        return {
            "rule": rule,
            "last": {k: v for k, v in (last or {}).items() if k != "at_s"} or None,
            "arms": arms,
            "gate": canary_gate(arms, cfg),
            "history": self.store.canary_history(10),
        }

    async def promote(self, tenant_id: str, body: dict[str, Any]) -> dict[str, Any]:
        mode = self.h.settings.mode
        if mode == "public":
            raise TuneError(403, "forbidden_in_mode", "the public sandbox never promotes")
        if self.running():
            raise TuneError(409, "tune_running", "a curation, run or comparison is running")
        stage = str(body.get("stage") or "canary")
        if stage not in ("canary", "full"):
            raise TuneError(400, "invalid_request", "stage must be canary or full")
        force = bool(body.get("force"))
        if force and mode != "dev":
            raise TuneError(403, "forbidden_in_mode", "force is for dev mode only")
        name, version = parse_ref(str(body.get("candidate") or ""), "model")
        model, mref = self.manifest("model", name, version)
        gate = model.get("gate") or {}
        if gate.get("status") != "passed" and not force:
            raise TuneError(409, "gate_failed", f"model/{name} has no passed gate", {"gate": gate})
        packs = _packs(body.get("packs")) or list(gate.get("packs") or [])
        against = str(body.get("against") or gate.get("against") or model.get("student") or "")
        if not packs:
            raise TuneError(400, "invalid_request", "packs: which packs the canary covers")
        if stage == "full":
            check = self.canary(tenant_id)
            rule = check["rule"] or {}
            if rule.get("split", {}).get("candidate_alias") != model["alias"]:
                raise TuneError(409, "canary_failed", "no canary of this model is running")
            if not check["gate"]["passed"] and not force:
                raise TuneError(409, "canary_failed", check["gate"]["reason"], {"canary": check})
            fraction = 1.0
        else:
            fraction = float(body.get("fraction", self.canary_cfg()["fraction"]))
            if not 0 <= fraction <= 1:
                raise TuneError(400, "invalid_request", "fraction must be from 0 to 1")
        canary = {
            "when": {"pack": packs},
            "split": {"base_alias": against, "candidate_alias": model["alias"],
                      "fraction": fraction},
        }  # fmt: skip
        text = with_alias(
            self._models_text(), model["alias"], model["alias_config"],
            f"{model['alias']}: model/{name}@v{mref['version']} (tune promote)",
        )  # fmt: skip
        text = with_canary(text, canary)
        note = f"tune promote {stage}: model/{name}@v{mref['version']} at {fraction}"
        out = await self._serve(tenant_id, text, packs, note, force=force)
        setting = {"stage": stage, "model": ref_text(mref, "model"), "fraction": fraction,
                   "packs": packs, "base_alias": against, "candidate_alias": model["alias"],
                   "by": f"user:{tenant_id}", "forced": force}  # fmt: skip
        self.store.canary_set(setting)
        if force:
            self._audit_forced(tenant_id, "tune_forced", setting)
        return {**out, "setting": setting, "canary": self.canary(tenant_id)}

    async def rollback(self, tenant_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.h.settings.mode == "public":
            raise TuneError(403, "forbidden_in_mode", "the public sandbox never rolls back")
        if self.running():
            raise TuneError(409, "tune_running", "a curation, run or comparison is running")
        rule = canary_of(self._models_text())
        if not rule:
            raise TuneError(409, "no_canary", "no canary rule is set")
        rule = {**rule, "split": {**rule["split"], "fraction": 0}}
        packs = list((rule.get("when") or {}).get("pack") or [])
        packs = packs if isinstance(packs, list) else [packs]
        text = with_canary(self._models_text(), rule)
        out = await self._serve(tenant_id, text, packs, "tune rollback: canary at 0")
        setting = {"stage": "rollback", "fraction": 0, "packs": packs,
                   "base_alias": rule["split"]["base_alias"],
                   "candidate_alias": rule["split"]["candidate_alias"],
                   "by": f"user:{tenant_id}"}  # fmt: skip
        self.store.canary_set(setting)
        return {**out, "setting": setting, "canary": self.canary(tenant_id)}

    async def _serve(
        self, tenant_id: str, text: str, packs: list[str], note: str, *, force: bool = False
    ) -> dict[str, Any]:
        """Edit models/models in the working set, then release each pack (its golden gate)."""
        from harnesswalk.ops import release as rel

        try:
            edit = rel.apply_edit(self.h, tenant_id, "models", "models", text, note=note)
        except rel.ArtifactInvalid as exc:
            raise TuneError(422, "invalid_artifact", str(exc)) from exc
        except rel.Busy as exc:
            raise TuneError(409, "turn_running", str(exc)) from exc
        releases = []
        for pack in packs:
            try:
                out = await rel.apply_create_gated(
                    self.h.view(pack), tenant_id, note=note,
                    force=force and self.h.settings.mode == "dev",
                )  # fmt: skip
            except rel.GateFailed as exc:
                raise TuneError(409, "gate_failed", str(exc), exc.detail) from exc
            except rel.Busy as exc:
                raise TuneError(409, "turn_running", str(exc)) from exc
            releases.append({"pack": pack, "release_id": out["release_id"],
                             "gate": (out.get("gate") or {}).get("status")})  # fmt: skip
        return {"models_version": edit["version"], "releases": releases}

    def _audit_forced(self, tenant_id: str, action: str, extra: dict[str, Any]) -> None:
        from types import SimpleNamespace

        from harnesswalk.control.audit import AuditEntry

        entry = AuditEntry(
            action=action, actor=f"user:{tenant_id}", turn_id="", trace_id=new_trace_id(),
            decision="forced", extra={k: v for k, v in extra.items() if k != "by"},
        )  # fmt: skip
        self.h.audit.append(SimpleNamespace(tenant_id=tenant_id), entry)

    # -- the canary ledger: counts per arm from the event stream -----------------------------------

    def _on_turn_event(self, event: dict[str, Any]) -> None:
        try:
            kind = event.get("type")
            turn_id = event.get("turn_id")
            if kind == "turn_start":
                pack = (event.get("summary") or {}).get("pack")
                if pack:
                    self._turn_packs[turn_id] = pack
            elif kind == "node_exit" and event.get("node_id") == "router":
                summary = event.get("summary") or {}
                if summary.get("arm") and turn_id not in self._canary_turns:
                    self._canary_turns.add(turn_id)
                    self.store.canary_turn(
                        event["tenant_id"], turn_id, self._turn_packs.get(turn_id),
                        summary["arm"], summary.get("model") or "",
                    )  # fmt: skip
            elif kind == "turn_end":
                self._turn_packs.pop(turn_id, None)
                if turn_id not in self._canary_turns:
                    return
                self._canary_turns.discard(turn_id)
                exit_ = (event.get("summary") or {}).get("exit")
                self.store.canary_end(event["tenant_id"], turn_id, event.get("status"), exit_)
        except Exception:  # the ledger never breaks a turn
            log.exception("canary ledger")

    def _on_ops_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "feedback_recorded" or not event.get("turn_id"):
            return
        with contextlib.suppress(Exception):
            bad = (event.get("summary") or {}).get("rating") == "bad"
            self.store.canary_bad(event["tenant_id"], event["turn_id"], bad)

    async def aclose(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        for unsubscribe in self._unsubscribe:
            with contextlib.suppress(Exception):
                unsubscribe()
        self.store.close()


class _NoVersion:
    version = 0


def canonical_pretty(manifest: dict[str, Any]) -> str:
    import json

    return json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _packs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [str(p) for p in value]


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def _abs(path: str, root: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else root / p


def summarize_manifest(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """A few fields of a manifest for listings."""
    if kind == "dataset":
        counts = data.get("counts") or {}
        return {"teacher": data.get("teacher"), "packs": data.get("packs"),
                "train": counts.get("train"), "val": counts.get("val"),
                "kept": counts.get("kept"), "turns": counts.get("turns")}  # fmt: skip
    if kind == "adapter":
        return {"student": data.get("student"), "trainer": data.get("trainer"),
                "dataset": ref_text(data.get("dataset"), "dataset"),
                "metrics": data.get("metrics")}  # fmt: skip
    gate = data.get("gate") or {}
    return {"alias": data.get("alias"), "adapter": ref_text(data.get("adapter"), "adapter"),
            "serve": data.get("serve"), "gate": gate.get("status")}  # fmt: skip


def cell_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean, min and max passed over a cell's samples (scorecard samples), the mean tool_calls
    split of its failed cases, and how often each case passed."""
    passed = [float((s.get("totals") or {}).get("passed", 0)) for s in samples]
    scored = [
        float((s.get("totals") or {}).get("cases", 0) - (s.get("totals") or {}).get("skipped", 0))
        for s in samples
    ]
    splits: Counter = Counter()
    per_case: Counter = Counter()
    for s in samples:
        for c in s.get("cases") or []:
            if c.get("tool_split"):
                splits[c["tool_split"]] += 1
            if c.get("status") == "passed":
                per_case[c["case_id"]] += 1
            else:
                per_case[c["case_id"]] += 0
    n = max(1, len(samples))
    return {
        "passed": mean(passed),
        "min": min(passed) if passed else 0,
        "max": max(passed) if passed else 0,
        "scored": max(scored) if scored else 0,
        "tool_split": {k: round(v / n, 2) for k, v in sorted(splits.items())},
        "cases": {k: round(v / n, 2) for k, v in sorted(per_case.items())},
        "run_ids": [s.get("run_id") for s in samples],
        "errors": [s.get("run_id") for s in samples if s.get("status") != "done"],
        "usd": round(sum(float((s.get("cost") or {}).get("usd") or 0) for s in samples), 6),
        "seconds": round(sum(float(s.get("seconds") or 0) for s in samples), 1),
    }


def gain_and_drop(arm: dict[str, Any], base: dict[str, Any], packs: list[str], canary: list[str]):
    gain = round(sum(arm[p]["passed"] - base[p]["passed"] for p in packs), 4)
    drop = {p: round(base[p]["passed"] - arm[p]["passed"], 4) for p in canary}
    return gain, drop


def gate_verdict(cells: dict[str, dict[str, Any]], req: dict[str, Any]) -> dict[str, Any]:
    """The model gate (tuning.md, Compare): beat the base, lose at most N on each canary pack."""
    base, cand = cells["base"], cells["candidate"]
    packs, canary = req["packs"], req["canary_packs"]
    errored = [rid for arm in cells.values() for cell in arm.values() for rid in cell["errors"]]
    gain, drop = gain_and_drop(cand, base, packs, canary)
    status, reason = "passed", None
    regressed = [p for p, d in drop.items() if d > req["canary_tolerance_cases"]]
    if errored:
        status, reason = "failed", "run_error"
    elif gain < req["min_gain_cases"]:
        status, reason = "failed", "below_min_gain"
    elif regressed:
        status, reason = "failed", "canary_regressed"
    out: dict[str, Any] = {
        "status": status, "reason": reason, "gain": gain, "canary_drop": drop,
        "regressed": regressed, "min_gain_cases": req["min_gain_cases"],
        "canary_tolerance_cases": req["canary_tolerance_cases"],
        "table": {arm: {p: c["passed"] for p, c in arm_cells.items()}
                  for arm, arm_cells in cells.items()},
    }  # fmt: skip
    if "prompt_arm" in cells:
        pgain, pdrop = gain_and_drop(cells["prompt_arm"], base, packs, canary)
        out["prompt_arm"] = {
            "gain": pgain,
            "canary_drop": pdrop,
            "canary_ok": all(d <= req["canary_tolerance_cases"] for d in pdrop.values()),
            "would_pass": pgain >= req["min_gain_cases"]
            and all(d <= req["canary_tolerance_cases"] for d in pdrop.values()),
        }
    return out


def canary_gate(arms: dict[str, dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    cand, base = arms["candidate"], arms["base"]
    need = int(cfg["min_turns"])
    if cand["turns"] < need:
        return {"passed": False, "reason": f"too_few_turns: {cand['turns']} of {need}"}
    worse = (cand["failure_rate"] or 0) - (base["failure_rate"] or 0)
    if worse > float(cfg["max_failure_increase"]):
        return {
            "passed": False,
            "reason": f"candidate_worse: failure rate {cand['failure_rate']} against"
            f" {base['failure_rate'] or 0}",
        }
    return {"passed": True, "reason": None}


ARM_LABELS = {"base": "base", "candidate": "candidate (tuned)", "prompt_arm": "prompt arm"}


def _num(x: float) -> str:
    return f"{x:g}"


def render_comparison(record: dict[str, Any]) -> str:
    """A comparison as Markdown: pass counts per pack and arm, the verdict, the tool split."""
    req, cells, verdict = record["request"], record.get("cells") or {}, record["verdict"]
    arms = [a for a in ("base", "candidate", "prompt_arm") if a in cells]
    cand = f"model/{req['candidate']['name']}@v{req['candidate']['version']}"
    lines = [
        f"Comparison {record['id']}: {cand} against `{req['against']}`,"
        f" {req['samples']} sample(s) per cell, model grading, temperature 0.",
        "",
        "| Pack | role | " + " | ".join(ARM_LABELS[a] for a in arms) + " |",
        "|---|---|" + "---|" * len(arms),
    ]
    for pack in [*req["packs"], *req["canary_packs"]]:
        role = "target" if pack in req["packs"] else "canary"
        row = []
        for arm in arms:
            c = cells[arm].get(pack)
            if c is None:
                row.append("n/a")
                continue
            spread = f" ({_num(c['min'])}-{_num(c['max'])})" if c["min"] != c["max"] else ""
            row.append(f"{_num(c['passed'])}/{_num(c['scored'])}{spread}")
        lines.append(f"| {pack} | {role} | " + " | ".join(row) + " |")
    lines += ["", f"Gate: **{verdict['status']}**" + (
        f" ({verdict['reason']})" if verdict.get("reason") else ""
    ) + f". Gain on the target packs {_num(verdict['gain'])} cases (needs"
        f" {_num(verdict['min_gain_cases'])}); canary drop "
        + ", ".join(f"{p} {_num(d)}" for p, d in verdict["canary_drop"].items())
        + f" (at most {_num(verdict['canary_tolerance_cases'])} each)."]  # fmt: skip
    if verdict.get("prompt_arm"):
        pa = verdict["prompt_arm"]
        lines.append(
            f"Prompt arm: gain {_num(pa['gain'])}, canary "
            + ("ok" if pa["canary_ok"] else "regressed")
            + f"; it {'would' if pa['would_pass'] else 'would not'} pass the same gate."
        )
    splits = sorted({k for arm in arms for p in req["packs"] for k in cells[arm][p]["tool_split"]})
    if splits:
        lines += ["", "`tool_calls` split on the target packs (failed cases, mean per sample):", "",
                  "| Bucket | " + " | ".join(ARM_LABELS[a] for a in arms) + " |",
                  "|---|" + "---|" * len(arms)]  # fmt: skip
        for key in splits:
            vals = [
                _num(round(sum(cells[a][p]["tool_split"].get(key, 0) for p in req["packs"]), 2))
                for a in arms
            ]
            lines.append(f"| {key} | " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"
