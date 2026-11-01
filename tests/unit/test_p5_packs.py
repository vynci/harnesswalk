"""P5 packs: discovery, a session per pack, tenant data per pack, `?pack=` on the ops routes.
Fake provider only. The conformance profile adds `conformance_alt` (spec/conformance/packs)."""

from pathlib import Path

import pytest
import yaml

from harnesswalk.config.loader import load_settings
from harnesswalk.control.gateway import GatewayError, TurnRequest
from harnesswalk.ops import pack_index
from harnesswalk.runtime import Harness, PackRuntime
from harnesswalk.types import Identity
from tests.spec.support import ROOT
from tests.unit.support import settings, write_fixture
from tests.unit.test_http_api import client

ALT = "conformance_alt"


def remember_fixtures(tmp_path: Path) -> Path:
    d = tmp_path / "fx"
    remember = {
        "tool_calls": [
            {
                "call_id": "c1",
                "name": "remember",
                "arguments": {"kind": "fact", "key": "locker code", "value": "4417"},
            }
        ]
    }
    write_fixture(d, "remember_code", [[remember, {"text": "Saved 4417."}]])
    write_fixture(d, "ask_code", [[{"text": "Let me check."}]])
    return d


def harness(tmp_path: Path, **overrides) -> Harness:
    return Harness(
        settings(
            tmp_path,
            "remember_code",
            fixtures=remember_fixtures(tmp_path),
            overrides={"context.stores": ["procedural", "session", "semantic"], **overrides},
        )
    )


async def turn(h: Harness, message: str, *, session: str, pack: str | None, fixture: str):
    state = await h.gateway.start_turn(
        TurnRequest(
            message=message,
            identity=Identity("t1", None, ["user"], anonymous=False),
            session_id=session,
            dataset=pack,
            fixture=fixture,
        )
    )
    await h.gateway.wait(state.ctx.turn_id, 10)
    return h.tracer.get_turn("t1", state.ctx.turn_id)


def semantic_hits(events: list[dict]) -> int:
    exit_ = next(e for e in events if e["type"] == "node_exit" and e["node_id"] == "semantic")
    return exit_["summary"]["hits"]


# -- discovery ------------------------------------------------------------------------------------


def test_discovery_reads_pack_yaml_prompts_and_counts(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    shop = root / "shop"
    (shop / "docs").mkdir(parents=True)
    (shop / "skills").mkdir()
    (shop / "pack.yaml").write_text(
        yaml.safe_dump({"title": "A shop", "domain": "retail", "feature": "approvals"})
    )
    (shop / "prompts.md").write_text("# Try these\n\n- Cancel order A-1\n2. `Refund A-2`\n")
    (shop / "docs" / "a.md").write_text("# A\n")
    (shop / "docs" / "b.pdf").write_bytes(b"%PDF-1.4")
    (shop / "skills" / "s.md").write_text("x")
    (shop / "facts.jsonl").write_text('{"k": 1}\n\n{"k": 2}\n')
    (shop / "tools.yaml").write_text("- name: a\n- name: b\n")
    (shop / "golden.jsonl").write_text('{"id": "a"}\n// a comment\n')
    (root / "legacy").mkdir()
    (root / "legacy" / "system_prompt.md").write_text("Hi")
    (root / "Bad Name").mkdir()
    (root / "Bad Name" / "pack.yaml").write_text("title: x")
    (root / "empty").mkdir()
    s = load_settings(ROOT, overrides={"packs.roots": [str(root)]}, env={})
    found = pack_index.discover(s)
    assert list(found) == ["legacy", "shop"]
    shop_info = found["shop"].summary("legacy")
    assert shop_info["title"] == "A shop" and shop_info["feature"] == "approvals"
    assert shop_info["prompts"] == ["Cancel order A-1", "Refund A-2"]
    assert shop_info["counts"] == {
        "docs": 2, "facts": 2, "episodes": 0, "skills": 1, "tools": 2, "golden": 1,
    }  # fmt: skip
    assert found["legacy"].summary("legacy")["default"] is True
    assert found["legacy"].summary("legacy")["title"] == "legacy"
    assert pack_index.pack_dir(s, "shop") == shop
    assert pack_index.pack_dir(s, "nowhere") == ROOT / "datasets" / "nowhere"


def test_the_first_root_wins(tmp_path: Path) -> None:
    for root, title in (("one", "first"), ("two", "second")):
        (tmp_path / root / "p").mkdir(parents=True)
        (tmp_path / root / "p" / "pack.yaml").write_text(f"title: {title}\nprompts: [Hi]\n")
    s = load_settings(
        ROOT, overrides={"packs.roots": [str(tmp_path / "one"), str(tmp_path / "two")]}, env={}
    )
    assert pack_index.find(s, "p").meta["title"] == "first"
    assert pack_index.find(s, "p").prompts() == ["Hi"]


@pytest.mark.anyio
async def test_get_packs_and_capabilities(tmp_path: Path) -> None:
    h = harness(tmp_path)
    async with client(h) as c:
        body = (await c.get("/v1/packs")).json()
        assert body["default"] == "_starter"
        by_id = {p["id"]: p for p in body["packs"]}
        assert set(by_id) == {"_starter", ALT}
        assert by_id["_starter"]["default"] and by_id["_starter"]["loaded"]
        assert by_id[ALT]["title"] == "Conformance alternate pack" and not by_id[ALT]["loaded"]
        assert by_id[ALT]["prompts"] == ["What is my locker code?"]
        caps = (await c.get("/v1/capabilities")).json()
        assert caps["features"]["packs"] is True
        assert caps["packs"] == {"default": "_starter", "ids": ["_starter", ALT]}
    await h.aclose()


# -- sessions -------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_session_on_a_pack(tmp_path: Path) -> None:
    h = harness(tmp_path)
    async with client(h) as c, client(h, "other") as o:
        r = await c.post("/v1/sessions", json={"pack": ALT})
        assert r.status_code == 201, r.text
        made = r.json()
        assert made["pack"] == ALT and made["tenant_id"] == "t1"
        assert made["session_id"].startswith("sess_") and "harnesswalk_session" in r.cookies
        assert isinstance(h.view(ALT), PackRuntime) and ALT in h.packs
        got = (await c.get(f"/v1/sessions/{made['session_id']}")).json()
        assert got == {**made, "turns": 0}
        assert (await o.get(f"/v1/sessions/{made['session_id']}")).status_code == 404
        assert (await c.post("/v1/sessions", json={"pack": "nope"})).status_code == 422
        assert (await c.post("/v1/sessions", json={"pack": 3})).status_code == 400
        default = (await c.post("/v1/sessions", json={})).json()
        assert default["pack"] == "_starter"
        listed = {p["id"]: p for p in (await c.get("/v1/packs")).json()["packs"]}
        assert listed[ALT]["loaded"]

        # A turn in the session runs on its pack; another pack in options is refused.
        r = await c.post(
            "/v1/turns",
            json={"message": "Hi", "session_id": made["session_id"], "fixture": "ask_code"},
        )
        assert r.status_code == 202
        await h.gateway.wait(r.json()["turn_id"], 10)
        summary = (await c.get(f"/v1/turns/{r.json()['turn_id']}")).json()
        assert summary["pack"] == ALT
        r = await c.post(
            "/v1/turns",
            json={
                "message": "Hi",
                "session_id": made["session_id"],
                "options": {"pack": "_starter"},
            },
        )
        assert r.status_code == 422
        unknown = {"message": "Hi", "options": {"pack": "x"}}
        assert (await c.post("/v1/turns", json=unknown)).status_code == 422
    await h.aclose()


@pytest.mark.anyio
async def test_facts_stay_in_their_pack(tmp_path: Path) -> None:
    h = harness(tmp_path)
    first = await turn(
        h, "Remember my locker code 4417", session="s1", pack=None, fixture="remember_code"
    )
    assert first[0]["summary"]["pack"] == "_starter"
    assert any(e["type"] == "memory_written" for e in first)
    other = await turn(h, "What is my locker code?", session="s2", pack=ALT, fixture="ask_code")
    assert other[0]["summary"]["pack"] == ALT and other[0]["summary"]["dataset"] == ALT
    assert semantic_hits(other) == 0
    back = await turn(h, "What is my locker code?", session="s1", pack=None, fixture="ask_code")
    assert semantic_hits(back) == 1
    rows = h.memory_db.query(
        "SELECT tenant_id, pack, key FROM semantic_facts WHERE tenant_id = 't1'"
    )
    assert [(r["pack"], r["key"]) for r in rows] == [("_starter", "locker code")]
    # The release stamp of a turn is its own pack's.
    assert (
        other[0]["summary"]["release"]["release_id"]
        == h.view(ALT).release.status()["active_release_id"]
    )
    await h.aclose()


@pytest.mark.anyio
async def test_a_reviewed_proposal_lands_in_the_sessions_pack(tmp_path: Path) -> None:
    h = harness(tmp_path, **{"memory_writer.gate": "review"})
    events = await turn(h, "Remember 4417", session="s9", pack=ALT, fixture="remember_code")
    assert events[0]["summary"]["pack"] == ALT
    async with client(h) as c:
        pending = (await c.get("/v1/memory/proposals")).json()["proposals"]
        assert len(pending) == 1
        r = await c.post(
            f"/v1/memory/proposals/{pending[0]['proposal_id']}", json={"decision": "accept"}
        )
        assert r.status_code == 200, r.text
    rows = h.memory_db.query("SELECT pack FROM semantic_facts WHERE tenant_id = 't1'")
    assert [r["pack"] for r in rows] == [ALT]
    await h.aclose()


def test_an_unknown_pack_is_refused_before_a_turn(tmp_path: Path) -> None:
    h = harness(tmp_path)
    with pytest.raises(KeyError):
        h.view("nope")
    with pytest.raises(GatewayError) as exc:
        h.gateway._validate(
            TurnRequest(message="Hi", identity=Identity("t1", None, ["user"], False), dataset="x")
        )
    assert exc.value.status == 422


# -- ?pack= on the ops routes ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_artifacts_releases_and_inspectors_per_pack(tmp_path: Path) -> None:
    h = harness(tmp_path)
    async with client(h, "dev") as c:
        alt = (await c.get("/v1/artifacts", params={"pack": ALT})).json()
        assert alt["pack"] == ALT
        assert (await c.get("/v1/artifacts", params={"pack": "nope"})).status_code == 422
        skill = "---\nname: lockers\ndescription: Locker codes.\n---\nAsk for the code.\n"
        r = await c.put("/v1/artifacts/skill/lockers", params={"pack": ALT}, json={"body": skill})
        assert r.status_code == 200, r.text
        assert r.json()["path"] == f"datasets/{ALT}/skills/lockers.md"
        assert "lockers" in h.view(ALT).procedural.skills
        assert "lockers" not in h.procedural.skills  # pack kinds are per pack
        default = (await c.get("/v1/artifacts")).json()
        assert all(a["name"] != "lockers" for a in default["artifacts"])

        node = (await c.get("/v1/nodes/procedural", params={"pack": ALT})).json()
        names = [i["name"] for i in node["inspector"]["items"]]
        assert "lockers" in names
        node = (await c.get("/v1/nodes/procedural")).json()
        assert "lockers" not in [i["name"] for i in node["inspector"]["items"]]
        assert (await c.get("/v1/nodes/procedural", params={"pack": "x"})).status_code == 422

        rels = (await c.get("/v1/releases", params={"pack": ALT})).json()
        assert rels["pack"] == ALT and rels["working_set_dirty"] is True
        made = await c.post("/v1/releases", json={"pack": ALT, "note": "lockers"})
        assert made.status_code == 201, made.text
        rid = made.json()["release_id"]
        assert made.json()["pack"] == ALT
        assert (await c.get(f"/v1/releases/{rid}")).json()["pack"] == ALT
        assert (await c.get("/v1/releases")).json()["active_release_id"] != rid
        docs = (await c.get("/v1/knowledge/docs", params={"pack": ALT})).json()
        assert docs["pack"] == ALT and docs["docs"]  # the profile's documents, ingested for ALT
        golden = (await c.get("/v1/evals/golden", params={"pack": ALT})).json()
        assert golden["pack"] == ALT and golden["cases"]
    await h.aclose()


@pytest.mark.anyio
async def test_a_pack_that_does_not_load_is_422(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    (root / "broken").mkdir(parents=True)
    (root / "broken" / "system_prompt.md").write_text("Hi")
    (root / "broken" / "tools.yaml").write_text("- name: x\n  nonsense: [\n")
    h = Harness(
        settings(
            tmp_path,
            "plain_answer",
            profile=None,
            overrides={"packs.roots": ["datasets", str(root)]},
        )
    )
    async with client(h) as c:
        assert "broken" in [p["id"] for p in (await c.get("/v1/packs")).json()["packs"]]
        r = await c.post("/v1/sessions", json={"pack": "broken"})
        assert r.status_code == 422 and "cannot be loaded" in r.json()["error"]["message"]
        r = await c.post("/v1/turns", json={"message": "Hi", "options": {"pack": "broken"}})
        assert r.status_code == 422
    assert "broken" not in h.packs
    await h.aclose()
