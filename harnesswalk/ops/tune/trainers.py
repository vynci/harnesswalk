"""Trainers: one interface in front of every way to train an adapter (spec/contracts/tuning.md).

`config/trainers.yaml` names them by alias, like providers:

    fake          deterministic and instant: a fixed loss curve, a fake weights hash, and a model
                  served by a `fake` provider alias (CI, the Guide, the public replay)
    homelab-trl   a GPU worker over ssh (deploy/homelab/): LoRA with TRL, merge, GGUF, Ollama
    local-mlx     documented, not implemented (a run is refused)

A trainer gets a `TrainJob` and reports each optimizer step through `on_step`; it returns a
`TrainResult` with what the adapter and model manifests need. It never edits a served artifact:
the harness records the result, and compare and promote decide whether it is served.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harnesswalk.ops.artifacts import canonical

StepFn = Callable[[dict[str, Any]], Awaitable[None]]

DEFAULT_CONFIG: dict[str, Any] = {
    "method": "lora",
    "rank": 16,
    "alpha": 32,
    "dropout": 0.05,
    "target_modules": "all-linear",
    "epochs": 2,
    "learning_rate": 0.0002,
    "max_seq_len": 4096,
    "batch_size": 1,
    "grad_accum": 8,
    "warmup_ratio": 0.05,
    "seed": 7,
}


class TrainerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class TrainJob:
    run_id: str
    name: str  # the adapter and model name
    trainer: str
    student: str  # the student's model alias
    student_cfg: dict[str, Any]  # its config/models.yaml entry
    base: dict[str, Any]  # trainers.yaml students.<alias>: repo, revision, renderer, parser
    dataset: dict[str, Any]  # the dataset manifest
    dataset_ref: dict[str, Any]  # {name, version, hash}
    files: dict[str, Path]  # train, val (local paths)
    config: dict[str, Any] = field(default_factory=dict)
    artifacts_dir: Path = Path("data/tune")


@dataclass
class TrainResult:
    config: dict[str, Any]
    metrics: dict[str, Any]
    weights: dict[str, Any]  # {location, sha256, bytes?}
    serve: dict[str, Any]  # {provider, tag?, digest?, quantization?, method, gguf_sha256?}
    modelfile: str
    alias_config: dict[str, Any]
    base_model: dict[str, Any]


def total_steps(examples: int, config: dict[str, Any]) -> int:
    per_step = max(1, int(config["batch_size"]) * int(config["grad_accum"]))
    return max(1, math.ceil(examples / per_step) * max(1, math.ceil(float(config["epochs"]))))


def tuned_alias(name: str) -> str:
    return f"tuned-{name.replace('_', '-')}"


class FakeTrainer:
    """Deterministic: the loss curve, the weights hash and the model follow from the dataset
    hash and the config. `outcome: good` serves a fake alias that answers like the fixtures
    (every case passes); `outcome: forgets` serves one that calls a tool when none is needed
    (`fake_style: over_call`), so the gate's refusal can be shown on the same loop."""

    kind = "fake"

    def __init__(self, alias: str, cfg: dict[str, Any]) -> None:
        self.alias = alias
        self.cfg = cfg

    async def train(self, job: TrainJob, on_step: StepFn) -> TrainResult:
        config = {**DEFAULT_CONFIG, **job.config, "method": "fake"}
        outcome = str(job.config.get("outcome") or self.cfg.get("outcome") or "good")
        if outcome not in ("good", "forgets"):
            raise TrainerError("invalid_request", "the fake trainer's outcome is good or forgets")
        config["outcome"] = outcome
        counts = job.dataset.get("counts") or {}
        steps = total_steps(int(counts.get("train") or 1), config)
        seed = int(hashlib.sha256(job.dataset_ref["hash"].encode()).hexdigest()[:6], 16)
        delay = float(self.cfg.get("step_delay_s", 0) or 0)
        started = time.monotonic()
        loss = eval_loss = 0.0
        for step in range(1, steps + 1):
            wobble = ((seed >> (step % 16)) % 7 - 3) / 400
            loss = round(0.32 + 1.9 * math.exp(-4.0 * step / steps) + wobble, 4)
            event = {
                "step": step,
                "total_steps": steps,
                "epoch": round(step / steps * float(config["epochs"]), 3),
                "loss": loss,
                "learning_rate": config["learning_rate"],
                "elapsed_s": round(time.monotonic() - started, 3),
                "eta_s": round(delay * (steps - step), 3),
                "phase": "train",
            }
            if step == steps or step % max(1, steps // 4) == 0:
                eval_loss = round(loss + 0.06, 4)
                event["eval_loss"] = eval_loss
            await on_step(event)
            if delay:
                await asyncio.sleep(delay)
        digest = hashlib.sha256(
            canonical({"dataset": job.dataset_ref["hash"], "config": config}).encode()
        ).hexdigest()
        params: dict[str, Any] = {"fake_style": "over_call"} if outcome == "forgets" else {}
        alias_config = {
            "provider": "fake",
            "label": f"Tuned {job.name} (fake trainer)",
            "context_tokens": int(job.student_cfg.get("context_tokens") or 32000),
            "price_in_per_mtok": 0,
            "price_out_per_mtok": 0,
            "tools": True,
        }
        if params:
            alias_config["params"] = params
        return TrainResult(
            config=config,
            metrics={
                "steps": steps,
                "train_loss": loss,
                "eval_loss": eval_loss,
                "wall_s": round(time.monotonic() - started, 3),
            },
            weights={"location": "none (the fake trainer stores no weights)", "sha256": digest},
            serve={"provider": "fake", "method": "fake"},
            modelfile=(
                f"# fake trainer, outcome {outcome}: served by the fake provider, which answers"
                " from each case's fixture"
                + (" and calls a tool when none is needed" if params else "")
                + f"\nFROM {job.student}\n"
            ),
            alias_config=alias_config,
            base_model={"repo": job.base.get("repo", job.student), "revision": "fake"},
        )


class StubTrainer:
    kind = "stub"

    def __init__(self, alias: str, cfg: dict[str, Any]) -> None:
        self.alias = alias
        self.cfg = cfg

    async def train(self, job: TrainJob, on_step: StepFn) -> TrainResult:
        note = self.cfg.get("note") or "this trainer is documented, not implemented"
        raise TrainerError("trainer_unavailable", f"trainer {self.alias}: {note}")


class SshTrainer:
    """A worker on another machine (deploy/homelab/README.md). The harness copies the dataset,
    the run config and the worker scripts to `<lab_dir>/runs/<run_id>/`, starts `worker.sh`
    detached, then polls `status.json` and `steps.jsonl` until `result.json` says done."""

    kind = "ssh"

    def __init__(self, alias: str, cfg: dict[str, Any], root: Path) -> None:
        self.alias = alias
        self.cfg = cfg
        self.root = root
        self.host = str(cfg["host"])
        self.lab = str(cfg.get("lab_dir", "~/harnesswalk-lab")).rstrip("/")
        self.poll_s = float(cfg.get("poll_s", 15))
        self.timeout_s = float(cfg.get("timeout_s", 6 * 3600))

    async def _run(self, *args: str, input_: bytes | None = None, check: bool = True) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_ is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(input_)
        if check and proc.returncode != 0:
            raise TrainerError(
                "trainer_error",
                f"{args[0]} exited {proc.returncode}: {err.decode(errors='replace')[-400:]}",
            )
        return out.decode(errors="replace")

    async def ssh(self, command: str, *, check: bool = True) -> str:
        return await self._run(
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.host, command,
            check=check,
        )  # fmt: skip

    async def copy(self, local: Path, remote: str) -> None:
        await self._run("scp", "-q", "-o", "BatchMode=yes", str(local), f"{self.host}:{remote}")

    async def train(self, job: TrainJob, on_step: StepFn) -> TrainResult:
        config = {**DEFAULT_CONFIG, **(self.cfg.get("defaults") or {}), **job.config}
        if self.lab.startswith("~"):  # quoted paths do not expand `~` on the worker
            home = (await self.ssh("echo $HOME")).strip()
            self.lab = home + self.lab[1:]
        run_dir = f"{self.lab}/runs/{job.run_id}"
        tag = f"{self.cfg.get('tag_prefix', 'hw')}-{job.name.replace('_', '-')}:{job.run_id}"
        spec = {
            "run_id": job.run_id,
            "name": job.name,
            "student": job.student,
            "base": job.base,
            "config": config,
            "tag": tag,
            "cuda_visible_devices": str(self.cfg.get("cuda_visible_devices", "1")),
            "ollama_container": self.cfg.get("ollama_container", "ollama"),
            "dataset": job.dataset_ref,
        }
        scripts = self.root / str(self.cfg.get("scripts", "deploy/homelab/trainer"))
        await self.ssh(f"mkdir -p {run_dir} {self.lab}/bin")
        for path in sorted(scripts.glob("*")):
            if path.is_file():
                await self.copy(path, f"{self.lab}/bin/{path.name}")
        await self.copy(job.files["train"], f"{run_dir}/train.jsonl")
        await self.copy(job.files["val"], f"{run_dir}/val.jsonl")
        await self._run(
            "ssh", "-o", "BatchMode=yes", self.host, f"cat > {run_dir}/run.json",
            input_=json.dumps(spec, indent=2).encode(),
        )  # fmt: skip
        await self.ssh(
            f"cd {run_dir} && nohup bash {self.lab}/bin/worker.sh {shlex.quote(run_dir)}"
            " > worker.log 2>&1 < /dev/null & echo started"
        )
        seen = 0
        started = time.monotonic()
        while True:
            await asyncio.sleep(self.poll_s)
            lines = await self.ssh(
                f"tail -n +{seen + 1} {run_dir}/steps.jsonl 2>/dev/null", check=False
            )
            for line in lines.splitlines():
                with contextlib.suppress(json.JSONDecodeError):
                    await on_step(json.loads(line))
                    seen += 1
            status_text = await self.ssh(f"cat {run_dir}/status.json 2>/dev/null", check=False)
            status: dict[str, Any] = {}
            with contextlib.suppress(json.JSONDecodeError):
                status = json.loads(status_text or "{}")
            if not status and time.monotonic() - started > 3 * self.poll_s:
                alive = await self.ssh(f"pgrep -f 'pipeline.py all {run_dir}' || true", check=False)
                if not alive.strip():
                    tail = await self.ssh(f"tail -n 20 {run_dir}/worker.log", check=False)
                    raise TrainerError("trainer_error", f"the worker is not running: {tail[-600:]}")
            if status.get("state") == "error":
                tail = await self.ssh(f"tail -n 20 {run_dir}/worker.log", check=False)
                raise TrainerError("trainer_error", f"{status.get('error')}: {tail[-600:]}")
            if status.get("state") == "done":
                break
            if time.monotonic() - started > self.timeout_s:
                raise TrainerError("trainer_timeout", f"no result after {self.timeout_s:.0f}s")
        result = json.loads(await self.ssh(f"cat {run_dir}/result.json"))
        alias_config = {
            k: v
            for k, v in job.student_cfg.items()
            if k in ("provider", "context_tokens", "price_in_per_mtok", "price_out_per_mtok",
                     "tools", "params")
        }  # fmt: skip
        alias_config.update(
            {"provider": "ollama", "name": result["serve"]["tag"], "label": f"Tuned {job.name}"}
        )
        return TrainResult(
            config=config,
            metrics=result.get("metrics") or {},
            weights=result.get("weights") or {},
            serve=result.get("serve") or {},
            modelfile=result.get("modelfile") or "",
            alias_config=alias_config,
            base_model={"repo": job.base.get("repo"), "revision": job.base.get("revision")},
        )


def build_trainer(alias: str, cfg: dict[str, Any], root: Path) -> Any:
    kind = cfg.get("kind")
    if kind == "fake":
        return FakeTrainer(alias, cfg)
    if kind == "ssh":
        return SshTrainer(alias, cfg, root)
    if kind == "stub":
        return StubTrainer(alias, cfg)
    raise TrainerError("invalid_request", f"trainer {alias} has an unknown kind {kind!r}")
