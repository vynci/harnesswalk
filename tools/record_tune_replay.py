"""Record the tuning loop the public site and the web mock replay (tune_replay.json).

A public server never curates, trains or promotes (spec/contracts/tuning.md): its `/v1/tune`
routes serve this file instead. Two sources:

    uv run python tools/record_tune_replay.py                 # the fake trainer's loop, offline
    uv run python tools/record_tune_replay.py --data-dir DIR  # a real run's data directory

The fake loop: curate `approvals` on ecommerce and logistics with the fake teacher, train it
twice on the fake trainer (`good`, and `forgets`, which the gate refuses), compare both against
the scripted student, promote the good one as a 0.5 canary, play eight chat turns, thumb the
candidate arm's replies down so the canary gate fails, try the full promote (refused), roll
back. A real data directory is dumped as it is (its artifacts, runs, comparisons and canary).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harnesswalk.config.loader import load_settings  # noqa: E402
from harnesswalk.control.gateway import TurnRequest  # noqa: E402
from harnesswalk.ops.tune import TuneError  # noqa: E402
from harnesswalk.runtime import Harness  # noqa: E402
from harnesswalk.types import Identity  # noqa: E402

OUT = ROOT / "config" / "fixtures" / "tune_replay.json"
TENANT = "dev"
TARGET = ["ecommerce", "logistics"]
CANARY = ["sports", "_starter"]


def harness(data: Path, provider: str = "fake") -> Harness:
    overrides = {"sandbox.level": "inprocess", "stores.embeddings.provider": "fake",
                 "dataset": "ecommerce"}  # fmt: skip
    return Harness(load_settings(provider=provider, fixture="plain_answer", data_dir=data,
                                 overrides=overrides))  # fmt: skip


async def chat(h: Harness, message: str, fixture: str, session: str) -> str:
    request = TurnRequest(
        message=message,
        identity=Identity(TENANT, None, ["user"], anonymous=False),
        session_id=session,
        channel="chat",
        provider="fake",
        fixture=fixture,
        dataset="ecommerce",
    )
    state = await h.gateway.start_turn(request)
    await h.gateway.wait(state.ctx.turn_id, timeout=30)
    return state.ctx.turn_id


async def fake_loop(h: Harness) -> list[dict[str, Any]]:
    t = h.tune
    story: list[dict[str, Any]] = []

    async def job(kind: str, start: Any, body: dict[str, Any]) -> dict[str, Any]:
        record = start(TENANT, body)
        await t.wait()
        record = t.store.get(record["id"]) or record
        story.append({"step": kind, "id": record["id"], "status": record["status"]})
        return record

    await job("curate", t.start_curation, {"name": "approvals", "packs": TARGET,
                                          "teacher": "fake-small", "variants": 2})  # fmt: skip
    good = await job(
        "run",
        t.start_run,
        {
            "dataset": "approvals",
            "trainer": "fake",
            "student": "fake-student",
            "name": "approvals_fake",
        },
    )
    bad = await job("run", t.start_run, {"dataset": "approvals", "trainer": "fake",
                                        "student": "fake-student", "name": "forgets_fake",
                                        "config": {"outcome": "forgets"}})  # fmt: skip
    for run in (good, bad):
        await job("compare", t.start_comparison, {
            "candidate": f"model/{run['request']['name']}", "against": "fake-student",
            "packs": TARGET, "canary_packs": CANARY, "samples": 1,
        })  # fmt: skip
    try:
        await t.promote(TENANT, {"candidate": "model/forgets_fake", "fraction": 0.5})
    except TuneError as exc:
        story.append({"step": "promote", "candidate": "model/forgets_fake", "status": exc.status,
                      "code": exc.code, "message": exc.message})  # fmt: skip
    out = await t.promote(TENANT, {"candidate": "model/approvals_fake", "fraction": 0.5})
    story.append({"step": "promote", "candidate": "model/approvals_fake", "status": 200,
                  "setting": out["setting"], "releases": out["releases"]})  # fmt: skip
    arms: dict[str, list[str]] = {"base": [], "candidate": []}
    for n in range(16):
        turn_id = await chat(h, "Where is my order QF-20417?", "order_lookup_qf20417", f"s{n}")
        row = h.tune.store.db.one(
            "SELECT arm FROM canary_turns WHERE turn_id = ?", (turn_id,)
        )  # fmt: skip
        arm = row["arm"] if row else "none"
        arms.setdefault(arm, []).append(turn_id)
        if arm == "candidate":  # in this replay a reviewer marks the tuned arm's replies wrong
            h.evals.online.feedback(TENANT, turn_id, "bad", "wrong carrier", "user:reviewer")
    story.append({"step": "chat", "turns": {k: len(v) for k, v in arms.items()},
                  "canary": t.canary(TENANT)})  # fmt: skip
    try:
        await t.promote(TENANT, {"candidate": "model/approvals_fake", "stage": "full"})
    except TuneError as exc:
        story.append({"step": "promote_full", "status": exc.status, "code": exc.code,
                      "message": exc.message})  # fmt: skip
    out = await t.rollback(TENANT, {})
    story.append({"step": "rollback", "status": 200, "setting": out["setting"]})
    return story


def dump(h: Harness, data: Path, story: list[dict[str, Any]], note: str) -> dict[str, Any]:
    t = h.tune

    def events(record: dict[str, Any]) -> list[dict[str, Any]]:
        return h.ops.list(record["tenant_id"], record["stream"], after=-1, limit=100000)

    artifacts = t.list_artifacts()
    details = {}
    for a in artifacts:
        details[f"{a['kind']}/{a['name']}"] = t.get_artifact(a["kind"], a["name"])
    doc = {
        "note": note,
        "overview": {**t.overview(TENANT), "replay": True},
        "artifacts": artifacts,
        "artifact_details": details,
        "curations": [{**r, "events": events(r)} for r in t.store.list("curation", None, 20)],
        "runs": [{**r, "events": events(r)} for r in t.store.list("run", None, 20)],
        "comparisons": t.store.list("comparison", None, 20),
        "canary": t.canary(None),
        "verify": t.verify(),
        "story": story,
    }
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str)
    return json.loads(text.replace(str(data), "data"))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", help="dump a real run's data directory instead")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    if args.data_dir:
        data = Path(args.data_dir)
        h = harness(data)
        try:
            doc = dump(h, data, [], "Recorded from a real tuning run (docs/tuning.md).")
        finally:
            await h.aclose()
    else:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            h = harness(data)
            try:
                story = await fake_loop(h)
                doc = dump(
                    h,
                    data,
                    story,
                    "The fake trainer's loop, recorded offline: a"
                    " scripted teacher, student and trainer (spec/contracts/tuning.md).",
                )
            finally:
                await h.aclose()
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    Path(args.out).write_text(text + "\n")
    print(f"wrote {args.out} ({Path(args.out).stat().st_size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
