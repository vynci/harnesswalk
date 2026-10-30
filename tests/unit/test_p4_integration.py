"""P4 integration: the tenant ops stream (GET /v1/events), the seams the UI relies on, and the
fixes from the integration review. Fake provider only."""

import asyncio
import contextlib
from pathlib import Path

import pytest

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.ops.ops_events import OPS_STREAM, OpsEvents
from harnesswalk.runtime import Harness
from tests.unit.support import run_turn, settings
from tests.unit.test_http_api import client, parse_sse


def starter(tmp_path: Path, **overrides) -> Harness:
    return Harness(settings(tmp_path, "plain_answer", profile=None, overrides=overrides))


# -- the tenant ops stream ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_tenant_stream_carries_every_ops_event_in_order(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c, client(h, "other") as o:
        events = await run_turn(h, "What are your support hours?", tenant="dev")
        turn_id = events[0]["turn_id"]
        assert (await c.post(f"/v1/turns/{turn_id}/feedback", json={"rating": "bad"})).is_success
        skill = (await c.get("/v1/artifacts/skill/returns")).json()["body"]
        put = await c.put("/v1/artifacts/skill/returns", json={"body": skill + "\nMore.\n"})
        assert put.status_code == 200
        assert (await c.post("/v1/releases", json={"note": "more"})).status_code == 201
        run = (
            await c.post(
                "/v1/evals/runs",
                json={"pack": "_starter", "suite": "golden", "cases": ["support_hours"]},
            )
        ).json()
        await h.evals.offline.wait(run["run_id"])

        r = await c.get("/v1/events", params={"after": "-1", "follow": "false"})
        assert r.headers["content-type"].startswith("text/event-stream")
        got = parse_sse(r.text)
        types = [e["type"] for e in got]
        assert types[:3] == ["feedback_recorded", "artifact_changed", "artifact_released"]
        assert types[3:] == ["eval_run_started", "eval_case_finished", "eval_run_finished"]
        cursors = [e["cursor"] for e in got]
        assert cursors == sorted(cursors) and len(set(cursors)) == len(cursors)
        assert {e["stream"] for e in got} == {OPS_STREAM, f"eval:{run['run_id']}"}
        assert got[0]["turn_id"] == turn_id and got[2]["summary"]["release_id"] == "r2"
        ids = [line[4:] for line in r.text.splitlines() if line.startswith("id: ")]
        assert ids == [str(x) for x in cursors]

        # From a cursor on, and with Last-Event-ID; without either the stream starts at now.
        rest = parse_sse(
            (await c.get("/v1/events", params={"after": cursors[2], "follow": "false"})).text
        )
        assert [e["type"] for e in rest] == types[3:]
        again = await c.get(
            "/v1/events", params={"follow": "false"}, headers={"Last-Event-ID": str(cursors[4])}
        )
        assert [e["type"] for e in parse_sse(again.text)] == ["eval_run_finished"]
        assert parse_sse((await c.get("/v1/events", params={"follow": "false"})).text) == []

        # Another tenant sees none of it, and cannot ask for this one.
        assert (
            parse_sse((await o.get("/v1/events", params={"after": "-1", "follow": "false"})).text)
            == []
        )
        assert (await o.get("/v1/events", params={"tenant": "dev"})).status_code == 404
        assert (await c.get("/v1/events", params={"after": "x"})).status_code == 400
    await h.aclose()


@pytest.mark.anyio
async def test_tail_wakes_on_an_emit_and_polls_for_other_processes(tmp_path: Path) -> None:
    db_path = tmp_path / "trace.db"
    mine = OpsEvents(Database(db_path, ""))
    theirs = OpsEvents(Database(db_path, ""))  # the CLI, say: same file, another connection
    got: list[str] = []

    async def read() -> None:
        async with contextlib.aclosing(mine.tail("dev", poll_s=0.05, keepalive_s=10)) as tail:
            async for item in tail:
                if item is not None:
                    got.append(item[2]["type"])
                if len(got) == 2:
                    return

    task = asyncio.create_task(read())
    await asyncio.sleep(0.05)
    summary = {"release_id": "r2", "artifacts": [], "pack": "_starter"}
    mine.emit("dev", OPS_STREAM, "artifact_released", "release", "done", summary)
    theirs.emit(
        "dev",
        OPS_STREAM,
        "artifact_changed",
        "release",
        "done",
        {"kind": "skill", "name": "returns", "version": 2},
    )
    await asyncio.wait_for(task, 5)
    assert got == ["artifact_released", "artifact_changed"]
    assert not mine._wakers


# -- inspector.artifact ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_every_artifact_backed_inspector_names_its_artifact(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:
        want = {
            "policy": ("policy", "policy"),
            "router": ("models", "models"),
            "registry": ("tools", "tools"),
            "context_builder": ("context", "context"),
            "model_layer": ("models", "models"),
        }
        for node_id, (kind, name) in want.items():
            ins = (await c.get(f"/v1/nodes/{node_id}")).json()["inspector"]
            ref = ins["artifact"]
            assert (ref["kind"], ref["name"], ref["version"]) == (kind, name, 1), node_id
            assert ref["url"] == f"/v1/artifacts/{kind}/{name}"
        items = (await c.get("/v1/nodes/procedural")).json()["inspector"]["items"]
        assert [(i["name"], i["artifact"]["kind"]) for i in items] == [
            ("system_prompt", "prompt"),
            ("order_status", "skill"),
            ("returns", "skill"),
        ]
        assert "artifact" not in (await c.get("/v1/nodes/loop")).json()["inspector"]
    await h.aclose()


# -- eval case turns in the trace query --------------------------------------------------------


@pytest.mark.anyio
async def test_kind_eval_lists_the_callers_case_turns_with_their_streams(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c, client(h, "other") as o:
        await run_turn(h, "What are your support hours?", tenant="dev")
        body = {"pack": "_starter", "suite": "golden", "cases": ["support_hours", "order_status"]}
        run_id = (await c.post("/v1/evals/runs", json=body)).json()["run_id"]
        await h.evals.offline.wait(run_id)

        chat = (await c.get("/v1/trace/turns", params={"kind": "chat"})).json()["turns"]
        assert len(chat) == 1 and chat[0]["kind"] == "chat"
        evals = (await c.get("/v1/trace/turns", params={"kind": "eval"})).json()
        rows = evals["turns"]
        assert {r["case_id"] for r in rows} == {"support_hours", "order_status"}
        assert all(r["kind"] == "eval" and r["run_id"] == run_id for r in rows)
        assert all(r["tenant_id"] == f"evalrun-{run_id}" for r in rows)
        one = next(r for r in rows if r["case_id"] == "order_status")
        assert one["events_url"] == f"/v1/evals/runs/{run_id}/cases/order_status/events"
        stream = parse_sse((await c.get(one["events_url"])).text)
        assert stream[0]["turn_id"] == one["turn_id"] and stream[-1]["type"] == "turn_end"
        receipt = (await c.get(f"/v1/trace/turns/{one['turn_id']}/receipt")).json()
        assert receipt["total"] == stream[-1]["cost"]
        page = (await c.get("/v1/trace/turns", params={"kind": "eval", "limit": 1})).json()
        nxt = await c.get("/v1/trace/turns", params={"kind": "eval", "after": page["next_after"]})
        assert len(nxt.json()["turns"]) == 1

        # Another tenant sees none of the run.
        assert (await o.get("/v1/trace/turns", params={"kind": "eval"})).json()["turns"] == []
        assert (await o.get(f"/v1/trace/turns/{one['turn_id']}/receipt")).status_code == 404
        assert (await o.get(one["events_url"])).status_code == 404
    await h.aclose()
