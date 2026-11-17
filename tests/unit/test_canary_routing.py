"""Groundwork for the tuning loop (0.2.0): the router's canary rule, the fake provider's scripted
students, candidate views, per-pack golden sets and the diagnose lever. Offline, fake provider."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from harnesswalk.config.loader import validate_split
from harnesswalk.execution.providers import ProviderRequest, ToolSchema
from harnesswalk.execution.providers.fake import styled
from harnesswalk.execution.router import Router, RouteRequest, bucket_of, with_canary
from harnesswalk.ops.eval.golden import golden_path
from harnesswalk.runtime import Harness
from harnesswalk.types import ConfigError, Message
from tests.unit.support import run_turn, settings

MODELS = {
    "base": {"provider": "fake"},
    "cand": {"provider": "fake"},
    "far": {"provider": "ollama"},
    "fake-small": {"provider": "fake"},
}
CANARY = {
    "when": {"pack": ["shop"]},
    "split": {"base_alias": "base", "candidate_alias": "cand", "fraction": 0.5},
}
RULES = [
    {"id": "public", "when": {"mode": "public"}, "use": ["fake-small"]},
    {"id": "default", "when": {}, "use": ["fake-small"]},
]


def router(canary: dict[str, Any] | None = None) -> Router:
    return Router(with_canary(RULES, canary or CANARY), MODELS, lambda p: p == "fake")


def req(turn_id: str, pack: str = "shop", **kw: Any) -> RouteRequest:
    return RouteRequest(step=1, step_kind="first", pack=pack, turn_id=turn_id, **kw)


def test_canary_rule_sits_after_public_and_splits_by_turn_id() -> None:
    assert [r["id"] for r in with_canary(RULES, CANARY)] == ["public", "canary", "default"]
    assert [r["id"] for r in with_canary(RULES[1:], CANARY)] == ["canary", "default"]
    r = router()
    arms: dict[str, int] = {}
    for n in range(200):
        route = r.route(req(f"turn-{n}"))
        want = "candidate" if bucket_of(f"turn-{n}") < 0.5 else "base"
        assert route.rule == "canary" and route.arm == want
        assert route.model == ("cand" if want == "candidate" else "base")
        assert route.alternatives == ["base" if want == "candidate" else "cand"]
        arms[want] = arms.get(want, 0) + 1
    assert 70 < arms["candidate"] < 130  # about half, the same every run
    assert r.route(req("turn-7")).arm == r.route(req("turn-7")).arm


def test_canary_fraction_ends_and_other_packs() -> None:
    none = {**CANARY, "split": {**CANARY["split"], "fraction": 0}}
    every = {**CANARY, "split": {**CANARY["split"], "fraction": 1.0}}
    assert {router(none).route(req(f"t{n}")).arm for n in range(30)} == {"base"}
    assert {router(every).route(req(f"t{n}")).arm for n in range(30)} == {"candidate"}
    assert router().route(req("t1", pack="other")).rule == "default"


def test_canary_skips_eval_turns_pins_and_unusable_arms() -> None:
    r = router()
    assert r.route(req("t1", internal=True)).rule == "default"
    assert r.pinned("fake-small").route(req("t1")).rule == "default"
    far = {"when": {"pack": "shop"}, "split": {"base_alias": "far", "candidate_alias": "far"}}
    assert router(far).route(req("t1")).rule == "default"  # neither arm usable: the next rule
    half = {**CANARY, "split": {**CANARY["split"], "candidate_alias": "far", "fraction": 1.0}}
    route = router(half).route(req("t1"))
    assert route.model == "base" and route.arm == "base" and route.alternatives == []


def test_validate_split() -> None:
    validate_split(CANARY, MODELS)
    for bad in (
        {**CANARY, "use": ["base"]},
        {"split": {"base_alias": "nope", "candidate_alias": "cand"}},
        {"split": {"base_alias": "base", "candidate_alias": "cand", "fraction": 1.5}},
        {"split": {"base_alias": "base", "candidate_alias": "cand", "fraction": True}},
    ):
        with pytest.raises(ConfigError):
            validate_split(bad, MODELS)


def test_a_served_canary_routes_the_packs_turns(tmp_path: Path) -> None:
    canary = {"when": {"pack": "_starter"},
              "split": {"base_alias": "fake-small", "candidate_alias": "fake-student",
                        "fraction": 1.0}}  # fmt: skip
    h = Harness(settings(tmp_path, overrides={"models.router.canary": canary}))

    async def go() -> list[dict[str, Any]]:
        try:
            return await run_turn(h, "What are your support hours?")
        finally:
            await h.aclose()

    import asyncio

    events = asyncio.run(go())
    router_exit = next(e for e in events if e["type"] == "node_exit" and e["node_id"] == "router")
    assert router_exit["summary"]["rule"] == "canary"
    assert router_exit["summary"]["arm"] == "candidate"
    assert router_exit["summary"]["model"] == "fake-student"
    with pytest.raises(ConfigError):
        settings(tmp_path, overrides={"models.router.canary": {"when": {}}})


def schemas() -> list[ToolSchema]:
    return [
        ToolSchema("lookup_order", "", {"type": "object"}, "read"),
        ToolSchema("cancel_order", "", {"type": "object"}, "destructive"),
    ]


def test_ask_first_turns_a_write_call_into_a_question() -> None:
    request = ProviderRequest(model="x", messages=[Message("user", "hi")], tools=schemas())
    write = {"tool_calls": [{"name": "cancel_order", "arguments": {"order_id": "A-1"}}]}
    read = {"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A-1"}}]}
    asked = styled("ask_first", write, request)
    assert "tool_calls" not in asked and "approval" in asked["text"]
    assert styled("ask_first", read, request) is read
    assert styled("unknown", write, request) is write


def test_over_call_calls_a_tool_on_a_plain_first_answer() -> None:
    first = ProviderRequest(model="x", messages=[Message("user", "hi")], tools=schemas())
    plain = {"text": "Hello."}
    assert styled("over_call", plain, first)["tool_calls"][0]["name"] == "lookup_order"
    after = [Message("user", "hi"), Message("tool", "{}", name="lookup_order")]
    later = ProviderRequest(model="x", messages=after, tools=schemas())
    assert styled("over_call", plain, later) is plain


def test_golden_paths_name_one_packs_set(tmp_path: Path) -> None:
    s = settings(
        tmp_path, overrides={"evals.golden_paths": {"alt": "datasets/sports/golden.jsonl"}}
    )
    assert golden_path(s, "alt").name == "golden.jsonl" and "sports" in str(golden_path(s, "alt"))
    assert golden_path(s, "_starter").name == "golden.jsonl"


@pytest.mark.anyio
async def test_candidate_view_serves_a_changed_body_and_stores_nothing(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, profile=None, overrides={"dataset": "ecommerce"}))
    try:
        prompt = h.release.store.get("ecommerce", "prompt", "system_prompt").body
        view = h.candidate_view("ecommerce", {("prompt", "system_prompt"): prompt + "\n\nEXTRA"})
        try:
            assert view.procedural.system_prompt.endswith("EXTRA")
            before = h.release.store.get("ecommerce", "prompt", "system_prompt")
            assert before.body == prompt  # nothing was written
        finally:
            view.close()
    finally:
        await h.aclose()


@pytest.mark.anyio
async def test_diagnose_names_the_tune_lever(tmp_path: Path) -> None:
    h = Harness(
        settings(
            tmp_path,
            profile=None,
            overrides={"dataset": "ecommerce", "stores.embeddings.provider": "fake"},
        )
    )
    try:
        run = h.evals.offline.start("dev", "ecommerce", "model", model="fake-student",
                                    case_ids=["cancel_approved_qf20430"])  # fmt: skip
        await h.evals.offline.wait(run["run_id"])
        d = h.evals.diagnose.case("dev", run["run_id"], "cancel_approved_qf20430")
        assert d["layer"] == "model" and d["levers"] == ["prompt_or_skill", "tune"]
        choice = next(e for e in d["evidence"] if e["kind"] == "tool_choice")
        assert choice["split"] in ("none called", "write tool not called")
    finally:
        await h.aclose()


def test_a_pinned_run_needs_a_configured_provider_not_a_healthy_one() -> None:
    healthy = {"fake": False}  # the passive health signal marked the provider down
    r = Router(
        RULES, MODELS, lambda p: p == "fake" and healthy["fake"], configured=lambda p: p == "fake"
    )
    route = r.pinned("fake-small").route(req("t1", pack="other"))
    assert route.model == "fake-small" and route.rule == "default"
    narrowed = r.restricted(lambda p: False, lambda p: p == "fake").pinned("fake-small")
    assert narrowed.route(req("t2", pack="other")).model == "fake-small"


def test_eval_case_turns_get_their_own_budget(tmp_path: Path) -> None:
    from harnesswalk.control.budgets import Budgets
    from harnesswalk.execution.loop import Limits

    b = Budgets(
        {"default": {"tokens_per_turn": 16000, "tokens_per_hour": 200000, "usd_per_turn": 0.05}},
        str(tmp_path / "b.db"),
        eval_case={"tokens_per_turn": 200000, "tokens_per_hour": 5000000},
    )
    chat = Limits.from_config({"budget_tokens": 16000}, b.for_turn("t"))
    case = Limits.from_config({"budget_tokens": 16000}, b.for_turn("t", internal=True))
    assert chat.budget_tokens == 16000 and case.budget_tokens == 200000
    assert b.for_turn("t", internal=True).limits["usd_per_turn"] == 0.05  # dollars still bind
    assert Budgets({}, str(tmp_path / "c.db")).for_turn("t", internal=True).limits == {}
