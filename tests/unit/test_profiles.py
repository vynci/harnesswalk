"""Harness profiles (spec/harness.md section 10): the files, the loader, the validator rules,
and one turn per profile on the fake provider with the switched-off nodes shown skipped."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from harnesswalk import diagram, packs, profiles
from harnesswalk.config.loader import load_settings
from harnesswalk.control.http_api import create_app
from harnesswalk.runtime import Harness
from harnesswalk.types import ConfigError
from tests.spec.support import ROOT
from tests.unit.support import exits, path_of, run_turn

LADDER = ["minimal", "guarded", "grounded", "remembered", "governed", "operated"]
ALL_PACKS = list(packs.discover(ROOT).values())


def _settings(tmp_path: Path, **kwargs: Any):
    overrides = {"sandbox.level": "inprocess", **kwargs.pop("overrides", {})}
    return load_settings(
        ROOT,
        provider="fake",
        fixture=kwargs.pop("fixture", "plain_answer"),
        overrides=overrides,
        data_dir=tmp_path / "data",
        env=kwargs.pop("env", {}),
        **kwargs,
    )


# -- the files --------------------------------------------------------------------------------


def test_the_six_profiles_form_a_ladder() -> None:
    ladder = profiles.all_profiles(ROOT)
    assert [p.id for p in ladder] == LADDER
    assert [p.rung for p in ladder] == [1, 2, 3, 4, 5, 6]
    assert ladder[-1].disable == frozenset() and ladder[-1].id == profiles.DEFAULT_PROFILE
    for lower, upper in zip(ladder, ladder[1:], strict=False):
        assert upper.disable < lower.disable, (lower.id, upper.id)  # each rung adds nodes
    nodes = diagram.nodes_by_id()
    switchable = {n for n, d in nodes.items() if d.get("can_disable")}
    assert ladder[0].disable == switchable  # minimal switches off everything that can go


def test_every_profile_keeps_the_nodes_that_never_switch_off() -> None:
    never = {"gateway", "trace", "cost", "context_builder", "memory", "procedural", "session",
             "loop", "model_layer", "router", "providers", "tools", "registry", "sandbox",
             "offline_eval", "artifacts"}  # fmt: skip
    for node_id in never:
        assert not diagram.nodes_by_id()[node_id].get("can_disable"), node_id
    for p in profiles.all_profiles(ROOT):
        assert not (p.disable & never), p.id
        assert {"procedural", "session"} <= set(p.stores), p.id


def _profile(**over: Any) -> dict[str, Any]:
    base = {"id": "x", "title": "X", "summary": "A test shape.", "rung": 3, "disable": [],
            "stores": list(profiles.STORES)}  # fmt: skip
    return {**base, **over}


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"disable": ["teleport"]}, "unknown node"),
        ({"disable": ["loop"]}, "cannot be disabled"),
        ({"disable": ["eval"]}, "must be off too"),
        ({"disable": ["policy"]}, "switched off together"),
        ({"disable": ["knowledge"]}, "stores must be"),
        ({"overrides": {"models.router.rules": []}}, "not allowed"),
        ({"rung": 7}, "rung"),
        ({"colour": "red"}, "unknown keys"),
        ({"id": "Bad Id"}, "id must match"),
    ],
)
def test_an_invalid_profile_is_a_load_error(over: dict[str, Any], needle: str) -> None:
    with pytest.raises(ConfigError, match=needle):
        profiles.parse(_profile(**over))


def test_public_mode_keeps_the_controls_on() -> None:
    minimal = profiles.load(ROOT, "minimal")
    public = minimal.off("public")
    assert not (public & set(profiles.PUBLIC_KEEP))
    assert minimal.off("dev") - public == set(profiles.PUBLIC_KEEP)
    assert minimal.stamp("public") == {"id": "minimal", "rung": 1, "off": sorted(public)}


# -- the loader -------------------------------------------------------------------------------


def test_a_pack_runs_with_the_profile_its_card_names(tmp_path: Path) -> None:
    s = _settings(tmp_path, overrides={"dataset": "healthcare"})
    assert s.profile.id == "grounded" and s.profile_override is None
    assert s.get("context.stores") == ["procedural", "session", "knowledge"]
    assert s.configured_stores == list(profiles.STORES)
    assert s.get("context.compaction.summarize") is False  # the profile's override
    assert _settings(tmp_path).profile.id == "operated"  # _starter
    assert _settings(tmp_path).get("context.stores") == list(profiles.STORES)


def test_name_path_and_env_precedence(tmp_path: Path) -> None:
    by_env = _settings(tmp_path, env={"HARNESSWALK_PROFILE": "minimal"})
    assert by_env.profile.id == "minimal" and by_env.profile_override == "minimal"
    by_arg = _settings(tmp_path, profile="guarded", env={"HARNESSWALK_PROFILE": "minimal"})
    assert by_arg.profile.id == "guarded"  # an argument wins over the environment, as before
    dotted = _settings(tmp_path, profile="guarded", overrides={"profile": "remembered"})
    assert dotted.profile.id == "remembered"  # a dotted override wins over both
    by_path = _settings(tmp_path, profile=str(ROOT / "config" / "profiles" / "governed.yaml"))
    assert by_path.profile.id == "governed"
    # A config overlay (no `rung`) is still a config overlay, and the pack's card still decides.
    overlay = _settings(tmp_path, profile=ROOT / "spec" / "conformance" / "profile.yaml")
    assert overlay.profile.id == "operated" and overlay.get("context.stores") == [
        "procedural",
        "session",
    ]
    # An explicit dotted key beats the profile's own override of it.
    kept = _settings(tmp_path, profile="minimal", overrides={"context.compaction.summarize": True})
    assert kept.get("context.compaction.summarize") is True


def test_an_unknown_or_invalid_profile_refuses_to_start(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no profile 'nope'"):
        _settings(tmp_path, env={"HARNESSWALK_PROFILE": "nope"})
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(_profile(disable=["loop"])))
    with pytest.raises(ConfigError, match="cannot be disabled"):
        _settings(tmp_path, profile=str(bad))


# -- the validator ----------------------------------------------------------------------------


def _copy(tmp_path: Path, pack_id: str, profile: str) -> packs.Pack:
    target = tmp_path / "datasets" / pack_id
    shutil.copytree(ROOT / "datasets" / pack_id, target)
    card = target / "pack.yaml"
    meta = yaml.safe_load(card.read_text())
    meta["profile"] = profile
    card.write_text(yaml.safe_dump(meta, sort_keys=False))
    return packs.load_pack(target)


def _profile_issues(pack: packs.Pack) -> list[str]:
    return [i.message for i in packs.validate(pack, ALL_PACKS, config_root=ROOT)
            if i.check == "profile"]  # fmt: skip


def test_every_shipped_pack_names_a_profile() -> None:
    mapping = {p.id: p.meta.get("profile") for p in ALL_PACKS}
    assert mapping == {
        "_starter": "operated",
        "device_fleet": "governed",
        "ecommerce": "governed",
        "finance": "operated",
        "gov_services": "grounded",
        "healthcare": "grounded",
        "helpdesk_lite": "minimal",
        "logistics": "governed",
        "long_session": "remembered",
        "sports": "grounded",
    }


def test_validator_paths_and_demonstrated_nodes_must_be_on(tmp_path: Path) -> None:
    found = "\n".join(_profile_issues(_copy(tmp_path, "healthcare", "guarded")))
    assert "needs 'knowledge'" in found  # a case that cites a document
    found = "\n".join(_profile_issues(_copy(tmp_path / "m", "healthcare", "minimal")))
    assert "demonstrates node 'input_guardrails'" in found
    found = "\n".join(_profile_issues(_copy(tmp_path / "f", "finance", "grounded")))
    assert "needs 'semantic'" in found  # checks.retrieved
    assert _profile_issues(_copy(tmp_path / "ok", "healthcare", "remembered")) == []


def test_validator_refuses_a_tool_that_needs_a_human_without_approval(tmp_path: Path) -> None:
    found = "\n".join(_profile_issues(_copy(tmp_path, "logistics", "remembered")))
    assert "tool reroute (write) needs a human approval" in found  # a policy rule, not a class
    assert "needs 'policy'" in found  # a golden path through approval
    found = "\n".join(_profile_issues(_copy(tmp_path / "e", "ecommerce", "grounded")))
    assert "tool cancel_order (destructive)" in found


def test_validator_flags_a_missing_or_unknown_profile(tmp_path: Path) -> None:
    pack = _copy(tmp_path, "sports", "nope")
    assert any("no profile 'nope'" in m for m in _profile_issues(pack))
    meta = yaml.safe_load((pack.path / "pack.yaml").read_text())
    del meta["profile"]
    (pack.path / "pack.yaml").write_text(yaml.safe_dump(meta))
    schema = [i.message for i in packs.validate(packs.load_pack(pack.path), ALL_PACKS,
              config_root=ROOT) if i.check == "schema"]  # fmt: skip
    assert any("'profile' is required" in m for m in schema)


# -- one turn per profile ---------------------------------------------------------------------

DIM_ON_THE_WALK = ("input_guardrails", "output_guardrails", "policy", "memory_writer",
                   "semantic", "episodic", "knowledge")  # fmt: skip


@pytest.mark.anyio
@pytest.mark.parametrize("profile_id", LADDER)
async def test_one_turn_per_profile_shows_the_switched_off_nodes_skipped(
    tmp_path: Path, profile_id: str
) -> None:
    """A read tool call on the starter pack under each profile: the same walk on every rung,
    each switched-off node entered and exited `skipped` with `disabled: true`, the others
    not, and `turn_start.summary.profile` naming the shape."""
    h = Harness(_settings(tmp_path, fixture="tool_call", profile=profile_id))
    try:
        events = await run_turn(h, "Where is order A-1001?")
    finally:
        await h.aclose()
    profile = profiles.load(ROOT, profile_id)
    start = events[0]["summary"]["profile"]
    assert start == {"id": profile_id, "rung": profile.rung, "off": sorted(profile.disable)}
    assert path_of(events)[:9] == [
        "gateway", "input_guardrails", "context_builder", "memory", "loop", "model_layer",
        "tools", "policy", "model_layer",
    ]  # fmt: skip
    for node_id in DIM_ON_THE_WALK:
        closes = exits(events, node_id)
        assert closes, (profile_id, node_id)
        off = node_id in profile.disable
        for e in closes:
            assert (e["summary"].get("disabled") is True) == off, (profile_id, node_id)
            if off:
                assert e["status"] == "skipped", (profile_id, node_id)
    tools = exits(events, "tools")[0]["summary"]
    assert tools["outcome"] == "ok" and tools["executed"] is True
    assert events[-1]["summary"]["exit"] == "answered"


@pytest.mark.anyio
async def test_a_destructive_call_under_a_profile_without_approval_is_never_asked(
    tmp_path: Path,
) -> None:
    """The request may switch approval off alone (dev): needs_approval becomes a deny."""
    h = Harness(_settings(tmp_path, fixture="approval_deny"))
    try:
        events = await run_turn(h, "Please cancel order A-1002.", disabled=["approval"])
        # The policy decision, the deny, then the memory writer's episode of the turn.
        assert h.audit.verify("t1") == {"ok": True, "records": 3}
    finally:
        await h.aclose()
    assert not [e for e in events if e["type"] == "approval_requested"]
    (approval,) = exits(events, "approval")
    assert approval["status"] == "skipped" and approval["summary"]["decision"] == "deny"
    assert exits(events, "tools")[0]["summary"]["executed"] is False


@pytest.mark.anyio
async def test_audit_off_keeps_nothing(tmp_path: Path) -> None:
    h = Harness(_settings(tmp_path, fixture="approval_deny", profile="minimal"))
    try:
        events = await run_turn(h, "Please cancel order A-1002.")
        records = h.audit.verify("t1")
    finally:
        await h.aclose()
    # Policy is off too (the three go together), so the call runs, allowed by rule disabled,
    # and the audit spans show where records would have been made.
    assert all(e["status"] == "skipped" for e in exits(events, "audit"))
    assert exits(events, "audit") and records == {"ok": True, "records": 0}


# -- HTTP -------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profiles_packs_and_the_dimmed_diagram_over_http(tmp_path: Path) -> None:
    import httpx

    h = Harness(_settings(tmp_path))
    app = create_app(h)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            body = (await c.get("/v1/profiles")).json()
            assert [p["id"] for p in body["profiles"]] == LADDER
            assert body["default"] == "operated" and body["override"] is None
            assert body["profiles"][0]["off"] == body["profiles"][0]["disable"]  # dev mode
            listed = {p["id"]: p["profile"] for p in (await c.get("/v1/packs")).json()["packs"]}
            assert listed["healthcare"] == "grounded" and listed["_starter"] == "operated"
            doc = (await c.get("/v1/diagram?pack=healthcare")).json()
            off = {n["id"] for n in doc["nodes"] if n["disabled"]}
            assert off == profiles.load(ROOT, "grounded").disable
            assert len(doc["nodes"]) == len(diagram.nodes_by_id())  # dimmed, never hidden
            assert doc["profile"]["id"] == "grounded" and doc["profile"]["rung"] == 3
            plain = (await c.get("/v1/diagram")).json()
            assert not any(n["disabled"] for n in plain["nodes"]) and "profile" not in plain
            assert (await c.get("/v1/diagram?pack=nope")).status_code == 422
    finally:
        await h.aclose()


def test_lessons_name_switchable_nodes_and_their_tries_run_on_a_rung_that_has_them() -> None:
    from harnesswalk.control.http_lessons import chapters

    nodes = diagram.nodes_by_id()
    for chapter in chapters(ROOT):
        for node_id in chapter["requires"]:
            assert nodes[node_id].get("can_disable"), (chapter["slug"], node_id)
        for t in [chapter["try"], *chapter["also"]]:
            if not t:
                continue
            pack = packs.get_pack(t["pack"], ROOT)
            off = profiles.load(ROOT, pack.profile).off("dev") - set(t.get("disable") or [])
            assert not (set(chapter["requires"]) & off), (chapter["slug"], t["pack"])
    required = {c["slug"]: c["requires"] for c in chapters(ROOT)}
    assert required["09-approval"] == ["policy", "approval"]
    assert json.dumps(required["03-context_builder"]) == "[]"
