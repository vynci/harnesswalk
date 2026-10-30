"""Release (P4): versioned artifacts, the working set, releases, rollback, and reloading."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from harnesswalk.ops.artifacts import ArtifactStore
from harnesswalk.ops.release import ReleaseManager
from harnesswalk.runtime import Harness
from tests.spec.support import event_validator
from tests.unit.support import run_turn, settings, write_fixture
from tests.unit.test_http_api import client

PROMPT_V1 = "You are the release test assistant. The secret word is apple."
PROMPT_V2 = "You are the release test assistant. The secret word is banana."


def fixture_dir(tmp_path: Path) -> Path:
    d = tmp_path / "fixtures"
    write_fixture(d, "says_apple", [[{"text": "apple", "expect": {"system_contains": "apple"}}]])
    write_fixture(d, "says_banana", [[{"text": "banana", "expect": {"system_contains": "banana"}}]])
    return d


def harness(tmp_path: Path, fixture: str = "says_apple", **kw) -> Harness:
    overrides = {"procedural.system_prompt": PROMPT_V1, **kw.pop("overrides", {})}
    return Harness(
        settings(tmp_path, fixture, fixtures=fixture_dir(tmp_path), overrides=overrides, **kw)
    )


def prompt_version(events: list[dict]) -> str:
    return next(e for e in events if e.get("node_id") == "procedural" and e["type"] == "node_exit")[
        "summary"
    ]["prompt_version"]


def test_first_start_seeds_the_working_set_and_releases_it(tmp_path: Path) -> None:
    h = harness(tmp_path)
    kinds = {(a["kind"], a["name"]) for a in h.release.listing()}
    assert {("prompt", "system_prompt"), ("policy", "policy"), ("context", "context")} <= kinds
    assert {("models", "models"), ("tools", "tools")} <= kinds
    assert all(a["version"] == 1 and a["updated_by"] == "seed" for a in h.release.listing())
    status = h.release.status()
    assert status == {
        "active_release_id": "r1",
        "working_set_dirty": False,
        "use_working_set": True,
    }
    assert h.release.store.verify("_starter") == {"ok": True, "releases": 1}
    assert h.procedural.system_prompt == PROMPT_V1


@pytest.mark.anyio
async def test_edit_release_rollback_round_trip(tmp_path: Path) -> None:
    h = harness(tmp_path)
    async with client(h, "dev") as c:
        first = await run_turn(h, "word?", tenant="dev")
        assert first[-1]["summary"]["reply"] == "apple"
        assert first[0]["summary"]["release"]["release_id"] == "r1"
        v1_stamp = prompt_version(first)

        r = await c.put(
            "/v1/artifacts/prompt/system_prompt", json={"body": PROMPT_V2, "note": "fruit change"}
        )
        assert r.status_code == 200, r.text
        edit = r.json()
        assert (edit["version"], edit["previous_version"], edit["applied"]) == (2, 1, True)
        assert "-You are the release test assistant. The secret word is apple." in edit["diff"]
        assert "+You are the release test assistant. The secret word is banana." in edit["diff"]
        assert edit["release"]["working_set_dirty"] is True
        assert h.procedural.system_prompt == PROMPT_V2  # served at once in dev

        h.providers["fake"] = h.turn_providers(None, "says_banana")["fake"]
        second = await run_turn(h, "word?", tenant="dev")
        assert second[-1]["summary"]["reply"] == "banana"
        assert prompt_version(second) != v1_stamp

        r = await c.post("/v1/releases", json={"note": "banana"})
        assert r.status_code == 201
        rel2 = r.json()
        assert rel2["release_id"] == "r2" and rel2["previous_release_id"] == "r1"
        assert [
            (d["kind"], d["from_version"], d["to_version"]) for d in rel2["diff_from_previous"]
        ] == [("prompt", 1, 2)]
        listed = (await c.get("/v1/releases")).json()
        assert [r["release_id"] for r in listed["releases"]] == ["r2", "r1"]
        assert listed["releases"][0]["active"] and not listed["working_set_dirty"]
        assert listed["verify"] == {"ok": True, "releases": 2}
        assert listed["releases"][0]["prev_hash"] == listed["releases"][1]["hash"]

        r = await c.post("/v1/releases/r1/rollback", json={})
        assert r.status_code == 200, r.text
        back = r.json()
        assert back["rollback_of"] == "r2" and back["active_release_id"] == "r1"
        assert back["reset"] == [
            {"kind": "prompt", "name": "system_prompt", "from_version": 2, "to_version": 3,
             "restored_version": 1}
        ]  # fmt: skip
        assert back["working_set_dirty"] is False
        art = (await c.get("/v1/artifacts/prompt/system_prompt")).json()
        assert art["version"] == 3 and art["body"] == PROMPT_V1 and art["updated_by"] == "rollback"
        assert [v["version"] for v in art["history"]] == [3, 2, 1]
        old = (await c.get("/v1/artifacts/prompt/system_prompt", params={"version": 2})).json()
        assert old["body"] == PROMPT_V2

        h.providers["fake"] = h.turn_providers(None, "says_apple")["fake"]
        third = await run_turn(h, "word?", tenant="dev")
        assert third[-1]["summary"]["reply"] == "apple"
        assert prompt_version(third) == v1_stamp  # the content stamp of the old version

        ops = (await c.get("/v1/trace/ops-events")).json()["events"]
        assert [e["type"] for e in ops] == [
            "artifact_changed", "artifact_released", "artifact_released"
        ]  # fmt: skip
        assert ops[2]["summary"]["rollback_of"] == "r2"
        assert ops[2]["summary"]["release_id"] == "r1"
        validator = event_validator()
        for event in ops:
            assert event["turn_id"] is None and not list(validator.iter_errors(event))
    await h.aclose()


@pytest.mark.anyio
async def test_edits_are_validated(tmp_path: Path) -> None:
    h = harness(tmp_path)
    async with client(h, "dev") as c:

        async def put(kind: str, name: str, body: str) -> tuple[int, dict]:
            r = await c.put(f"/v1/artifacts/{kind}/{name}", json={"body": body})
            return r.status_code, r.json()

        assert (await put("policy", "policy", "rules: [unclosed"))[0] == 422
        status, body = await put("policy", "policy", "rules:\n  - {id: x, decision: maybe}\n")
        assert status == 422 and body["error"]["code"] == "invalid_artifact"
        current = yaml.safe_load((await c.get("/v1/artifacts/models/models")).json()["body"])
        providers = yaml.safe_dump({"providers": current["providers"]})
        status, body = await put(
            "models",
            "models",
            providers + "models: {}\nrouter:\n  rules:\n    - {id: d, when: {}, use: [nope]}\n",
        )
        assert status == 422 and "nope" in body["error"]["message"]  # the dry run caught it
        assert (await put("tools", "tools", "- name: x\n"))[0] == 422
        assert (await put("skill", "Bad Name", "x"))[0] == 404
        assert (await put("prompt", "other_pack", "x"))[0] == 404
        assert (await put("nonsense", "x", "x"))[0] == 404
        assert (await put("prompt", "system_prompt", "   "))[0] == 422
        status, body = await put("skill", "returns", "---\nname: refunds\n---\nBody")
        assert status == 422 and "refunds" in body["error"]["message"]
        r = await c.put("/v1/artifacts/prompt/system_prompt", content=b"{}")  # no content type
        assert r.status_code == 415
        status, body = await put("prompt", "system_prompt", PROMPT_V1)  # the same body again
        assert status == 200 and body["unchanged"] and body["version"] == 1
        assert (await c.get("/v1/artifacts/skill/nope")).status_code == 404
        listed = (await c.get("/v1/artifacts")).json()
        assert all(a["version"] == 1 for a in listed["artifacts"])
    await h.aclose()


@pytest.mark.anyio
async def test_a_new_skill_reaches_the_skills_index(tmp_path: Path) -> None:
    h = harness(tmp_path)
    async with client(h, "dev") as c:
        body = "---\nname: shipping\ndescription: Where parcels ship from.\n---\nToronto.\n"
        r = await c.put("/v1/artifacts/skill/shipping", json={"body": body})
        assert r.status_code == 200 and r.json()["version"] == 1
    assert "- shipping: Where parcels ship from." in h.procedural.always_text()
    assert h.registry.get("read_skill") is not None
    await h.aclose()


@pytest.mark.anyio
async def test_a_policy_edit_applies_to_the_next_turn(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "tool_call", profile=None, mode="dev"))
    async with client(h, "dev") as c:
        policy = (await c.get("/v1/artifacts/policy/policy")).json()["body"]
        edited = policy.replace(
            "- { id: dev.read.allow,          modes: [dev, private, public], action_classes:"
            " [read], decision: allow }",
            "- { id: dev.read.deny,           modes: [dev], action_classes: [read],"
            " decision: deny }",
        )
        assert edited != policy
        r = await c.put("/v1/artifacts/policy/policy", json={"body": edited})
        assert r.status_code == 200, r.text
        events = await run_turn(h, "Where is my order A-1001?", tenant="dev")
        decided = [e for e in events if e.get("node_id") == "policy" and e["type"] == "node_exit"]
        assert decided[0]["summary"]["decision"] == "deny"
        assert decided[0]["summary"]["rule"] == "dev.read.deny"
        inspector = (await c.get("/v1/nodes/policy")).json()["inspector"]
        assert inspector["artifact"] == {
            "kind": "policy", "name": "policy", "version": 2, "url": "/v1/artifacts/policy/policy"
        }  # fmt: skip
        assert "dev.read.deny" in inspector["content"]
    await h.aclose()


@pytest.mark.anyio
async def test_changes_wait_for_running_turns(tmp_path: Path) -> None:
    h = harness(tmp_path)
    from harnesswalk.control.gateway import TurnRequest
    from harnesswalk.types import Identity

    state = await h.gateway.start_turn(
        TurnRequest(message="hi", identity=Identity("dev", None, ["user"], False), step_mode=True)
    )
    for _ in range(100):  # wait for the first breakpoint
        if state.ctx.emit.breakpoints.paused_node:
            break
        await asyncio.sleep(0.01)
    async with client(h, "dev") as c:
        r = await c.put("/v1/artifacts/prompt/system_prompt", json={"body": PROMPT_V2})
        assert r.status_code == 409 and r.json()["error"]["code"] == "turn_running"
        assert (await c.post("/v1/releases/r1/rollback", json={})).status_code == 409
        assert (await c.post("/v1/releases", json={})).status_code == 201  # serves the same
    state.ctx.emit.breakpoints.finish()
    await h.gateway.wait(state.ctx.turn_id, 5)
    await h.aclose()


@pytest.mark.anyio
async def test_public_mode_reads_but_never_edits(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer", profile=None, mode="public"))
    import httpx

    from harnesswalk.control.http_api import create_app

    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.get("/v1/artifacts")).status_code == 200
        r = await c.put("/v1/artifacts/prompt/system_prompt", json={"body": "x"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_in_mode"
        assert (await c.post("/v1/releases", json={})).status_code == 403
        assert (await c.post("/v1/releases/r1/rollback", json={})).status_code == 403
    assert h.release.use_working_set is False
    await h.aclose()


def test_a_changed_source_is_imported_and_released_outside_dev(tmp_path: Path) -> None:
    a = harness(tmp_path)
    a.release.close()
    b = harness(tmp_path, overrides={"procedural.system_prompt": PROMPT_V2})
    art = b.release.artifact("prompt", "system_prompt")
    assert (art["version"], art["updated_by"]) == (2, "disk")
    assert b.release.status()["working_set_dirty"] is True  # dev: imported, not released
    assert b.procedural.system_prompt == PROMPT_V2
    b.release.close()

    private = tmp_path / "private"
    s1 = settings(private, "plain_answer", overrides={"procedural.system_prompt": PROMPT_V1})
    s1.data["mode"] = "private"
    ReleaseManager(s1).close()
    s2 = settings(private, "plain_answer", overrides={"procedural.system_prompt": PROMPT_V2})
    s2.data["mode"] = "private"
    rm = ReleaseManager(s2)
    rels = rm.store.releases("_starter")
    assert [r.release_id for r in rels] == ["r2", "r1"]
    assert rels[0].note.startswith("imported at start-up: prompt/system_prompt")
    assert not rm.status()["working_set_dirty"]
    rm.close()


def test_the_release_chain_detects_tampering(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path / "releases.db"))
    store.put("p", "prompt", "p", "one", by="t")
    store.create_release("p", by="t", note="a")
    store.put("p", "prompt", "p", "two", by="t")
    store.create_release("p", by="t", note="b")
    assert store.verify("p") == {"ok": True, "releases": 2}
    store.db.execute("UPDATE releases SET note = 'edited' WHERE release_id = 'r1'")
    assert store.verify("p") == {"ok": False, "releases": 2, "broken_at": "r1"}
    store.close()


@pytest.mark.anyio
async def test_a_change_from_another_process_reloads_before_the_next_turn(tmp_path: Path) -> None:
    server = harness(tmp_path)
    other = ArtifactStore(str(tmp_path / "data" / "releases.db"))  # the CLI, say
    other.put("_starter", "prompt", "system_prompt", PROMPT_V2, by="cli")
    server.providers["fake"] = server.turn_providers(None, "says_banana")["fake"]
    events = await run_turn(server, "word?", tenant="dev")
    assert events[-1]["summary"]["reply"] == "banana"
    other.close()
    await server.aclose()


def test_release_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    from harnesswalk.__main__ import main

    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path))
    assert main(["release", "list", "--provider", "fake"]) == 0
    assert "* r1" in capsys.readouterr().out
    store = ArtifactStore(str(tmp_path / "releases.db"))
    body = store.get("_starter", "skill", "returns").body
    store.put("_starter", "skill", "returns", body + "\nOne more line.\n", by="test")
    store.close()
    assert main(["release", "create", "--provider", "fake", "--note", "more"]) == 0
    out = capsys.readouterr().out
    assert "created r2" in out and "skill/returns 1 -> 2" in out
    assert main(["release", "diff", "r2", "--provider", "fake"]) == 0
    assert "+One more line." in capsys.readouterr().out
    assert main(["release", "rollback", "r1", "--provider", "fake"]) == 0
    assert "skill/returns 2 -> 3" in capsys.readouterr().out
    assert main(["release", "list", "--provider", "fake", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["active_release_id"] == "r1" and not data["working_set_dirty"]
