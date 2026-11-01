"""Public mode (P6): the model allowlist, capabilities, the shared hosted budget and BYO keys.

Contract: plans phase-6/CONTRACT.md sections 2 to 4, spec/contracts/http_api.md "P6 additions".
Fake provider only; the Anthropic provider, where a test needs it, answers from a mock.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import httpx
import pytest

from harnesswalk.config.loader import load_settings
from harnesswalk.control.http_api import create_app
from harnesswalk.ops import logs
from harnesswalk.ops.budget import HostedBudget
from harnesswalk.runtime import Harness
from harnesswalk.types import ConfigError
from tests.public.support import (
    SITE_KEY_ENV,
    client,
    dumps,
    every_file_bytes,
    mock_anthropic,
    node_exits,
    public_harness,
    run,
)
from tests.spec.support import ROOT, check_stream
from tests.unit.support import write_fixture

MODEL_KEYS = {"alias", "label", "provider", "default", "available", "reason"}


# -- configuration (section 1) ----------------------------------------------------------------


def test_public_mode_refuses_to_start_without_a_secret(tmp_path: Path) -> None:
    s = load_settings(ROOT, mode="public", provider="fake", data_dir=tmp_path / "d", env={})
    h = Harness(s)
    with pytest.raises(ConfigError, match="HARNESSWALK_SECRET"):
        create_app(h)
    assert "secret" not in repr(s) and "HARNESSWALK_SECRET" not in str(s.data)


def test_env_overrides_and_the_public_rule(tmp_path: Path) -> None:
    env = {
        "HARNESSWALK_SECRET": "s3cret-value",
        "HARNESSWALK_PUBLIC_BUDGET_USD": "2.5",
        "HARNESSWALK_TRUST_PROXY": "1",
        "HARNESSWALK_LOG_FORMAT": "json",
    }
    s = load_settings(ROOT, mode="public", env=env, data_dir=tmp_path / "d")
    assert s.secret == "s3cret-value" and "s3cret-value" not in str(s.data)
    assert s.get("public.hosted_budget_usd_per_day") == 2.5
    assert s.get("gateway.trust_proxy") is True and s.get("logging.format") == "json"
    rule = next(r for r in s.get("models.router.rules") if r["id"] == "public")
    assert rule["use"] == ["hosted-haiku", "hosted-sonnet", "fake-small"]  # default first
    # public.models implies the providers: exactly those, the hosted one without `enabled`.
    built = {
        n for n in ("fake", "ollama", "anthropic", "openai_compatible") if s.provider_configured(n)
    }
    assert built == {"fake", "anthropic"}
    with pytest.raises(ConfigError, match="BUDGET_USD"):
        load_settings(ROOT, env={"HARNESSWALK_PUBLIC_BUDGET_USD": "lots"})
    with pytest.raises(ConfigError, match="unknown model"):
        load_settings(ROOT, mode="public", overrides={"public.models": ["nope"]}, env={})
    with pytest.raises(ConfigError, match="default_model"):
        load_settings(ROOT, mode="public", overrides={"public.default_model": "local-8b"}, env={})


# -- capabilities (section 2) -----------------------------------------------------------------


@pytest.mark.anyio
async def test_capabilities_public_object_in_public_mode(tmp_path: Path) -> None:
    h = public_harness(tmp_path)
    async with client(h) as c:
        caps = (await c.get("/v1/capabilities")).json()
        assert "harnesswalk_tenant" in c.cookies  # the session clock starts at first load
    pub = caps["public"]
    assert set(pub) == {
        "session_expires_at", "ttl_s", "limits", "models", "budget", "byo_key_bypasses_budget",
        "fixtures", "default_fixture",
    }  # fmt: skip
    assert pub["session_expires_at"].endswith("Z") and pub["ttl_s"] == 7200
    assert pub["limits"] == {
        "per_ip_per_hour": 30, "remaining_this_hour": 30, "max_steps": 8,
        "budget_tokens": 16000, "max_message_chars": 8000,
    }  # fmt: skip
    assert [m["alias"] for m in pub["models"]] == ["hosted-haiku", "hosted-sonnet", "fake-small"]
    assert all(set(m) == MODEL_KEYS for m in pub["models"])
    by = {m["alias"]: m for m in pub["models"]}
    assert by["hosted-haiku"]["label"] == "Claude Haiku 5.5"
    assert by["fake-small"]["label"] == "Fake model (scripted)"
    # Started with --provider fake: no Claude, so the default a turn would use is fake-small.
    assert (
        by["hosted-haiku"]["reason"] == "provider_unconfigured"
        and not by["hosted-haiku"]["available"]
    )
    assert by["fake-small"] == {
        **by["fake-small"],
        "available": True,
        "default": True,
        "reason": None,
    }
    assert pub["budget"]["status"] == "ok" and pub["budget"]["spent_fraction"] == 0
    assert pub["budget"]["resets_at"].endswith("T00:00:00Z")
    assert "usd" not in str(pub["budget"]) and pub["byo_key_bypasses_budget"] is True
    # The scripted model's fixtures, in public mode too: names and one-line descriptions only.
    fixtures = {f["name"]: f for f in pub["fixtures"]}
    assert pub["default_fixture"] == "public_demo" and "public_demo" in fixtures
    assert all(set(f) == {"name", "description", "packs"} for f in pub["fixtures"])
    assert (
        fixtures["public_demo"]["packs"] == [] and "finance" in fixtures["tool_nav_hcp02"]["packs"]
    )
    assert fixtures["approval_approve"]["description"] == (
        "A destructive tool call that needs approval; the human approves."
    )
    assert fixtures["public_demo"]["description"].startswith("The scripted model a public")
    assert "\n" not in fixtures["public_demo"]["description"]
    assert fixtures["golden_pii"]["description"] is None  # a JSONL fixture has none
    assert str(ROOT) not in dumps(pub["fixtures"])


@pytest.mark.anyio
async def test_capabilities_public_object_in_dev_mode(tmp_path: Path) -> None:
    from tests.unit.support import harness

    h = harness(tmp_path)
    async with client(h) as c:
        pub = (await c.get("/v1/capabilities")).json()["public"]
    assert pub["session_expires_at"] is None
    assert pub["budget"] == {"status": "ok", "resets_at": None, "spent_fraction": 0}
    aliases = [m["alias"] for m in pub["models"]]
    assert "fake-small" in aliases and "local-embed" not in aliases  # every non-embedding alias
    fake = next(m for m in pub["models"] if m["alias"] == "fake-small")
    assert fake["available"] and fake["default"]
    assert next(m for m in pub["models"] if m["alias"] == "local-8b")["reason"] == (
        "provider_unconfigured"
    )
    assert pub["limits"]["remaining_this_hour"] == pub["limits"]["per_ip_per_hour"] == 30


@pytest.mark.anyio
async def test_site_key_makes_claude_available_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SITE_KEY_ENV, "site-key-value")
    h = public_harness(tmp_path, hosted=True)
    mock_anthropic(h)
    async with client(h) as c:
        pub = (await c.get("/v1/capabilities")).json()["public"]
        by = {m["alias"]: m for m in pub["models"]}
        assert by["hosted-haiku"]["available"] and by["hosted-haiku"]["default"]
        out = await run(c, h, {"message": "What are your support hours?"})
    model = node_exits(out["events"], "model_layer")[0]["summary"]
    assert (model["provider"], model["model"]) == ("anthropic", "hosted-haiku")
    assert h.hosted_budget.today()["calls"] == 1 and h.hosted_budget.today()["spent_usd"] > 0


# -- turn request (section 3) -----------------------------------------------------------------


@pytest.mark.anyio
async def test_model_allowlist(tmp_path: Path) -> None:
    h = public_harness(tmp_path)
    async with client(h) as c:
        ok = await run(c, h, {"message": "What are your support hours?", "model": "fake-small"})
        assert ok["response"].status_code == 202
        assert node_exits(ok["events"], "model_layer")[0]["summary"]["model"] == "fake-small"
        assert check_stream(ok["events"]) == []
        r = await c.post("/v1/turns", json={"message": "hi", "model": 3})
        assert r.status_code == 400
        for alias in ("local-8b", "compat-default", "local-embed", "nope"):
            r = await c.post("/v1/turns", json={"message": "hi", "model": alias})
            assert r.status_code == 400, alias
            assert r.json()["error"] == {
                "code": "invalid_request", "message": "model not available in public mode"
            }  # fmt: skip
        r = await c.post("/v1/turns", json={"message": "hi", "model": "hosted-sonnet"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "provider_not_configured"
        # The provider and fixture choices stay dev only.
        r = await c.post("/v1/turns", json={"message": "hi", "provider": "fake"})
        assert r.status_code == 400


@pytest.mark.anyio
async def test_a_public_turn_on_the_scripted_model_may_name_a_fixture(tmp_path: Path) -> None:
    h = public_harness(tmp_path)  # no site key: Auto resolves to fake-small too
    async with client(h) as c:
        body = {"message": "Where is order A-1001?", "model": "fake-small", "fixture": "tool_call"}
        out = await run(c, h, body)
        assert out["response"].status_code == 202
        events = out["events"]
        assert check_stream(events) == []
        assert events[0]["summary"]["fixture"] == "tool_call"
        models = [e["summary"]["model"] for e in node_exits(events, "model_layer")]
        assert models == ["fake-small", "fake-small"]
        assert events[-1]["summary"]["reply"] == (
            "Order A-1001 shipped on 24 October and is due to arrive on 30 October."
        )
        # Auto (no hint) while the router would pick the scripted model: accepted as well.
        auto = await run(
            c, h, {"message": "What are your support hours?", "fixture": "plain_answer"}
        )
        assert auto["response"].status_code == 202
        assert "9am to 5pm" in auto["events"][-1]["summary"]["reply"]
        # Without a fixture the turn plays the server's own (public_demo).
        plain = await run(c, h, {"message": "Hello?", "model": "fake-small"})
        assert "scripted fake model" in plain["events"][-1]["summary"]["reply"]


@pytest.mark.anyio
async def test_a_public_fixture_is_refused_off_the_scripted_model_or_by_an_unknown_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SITE_KEY_ENV, "site-key-value")
    h = public_harness(tmp_path, hosted=True)
    seen = mock_anthropic(h)
    dev_only = {
        "code": "invalid_request",
        "message": "provider and fixture can only be chosen in dev mode",
    }
    async with client(h) as c:
        # A hosted model, named or picked by Auto (the budget is fine): the dev-only refusal.
        for body in ({"model": "hosted-haiku"}, {}):
            r = await c.post("/v1/turns", json={"message": "hi", "fixture": "tool_call", **body})
            assert r.status_code == 400 and r.json()["error"] == dev_only, body
        # The provider stays dev only, fixture or not.
        r = await c.post(
            "/v1/turns", json={"message": "hi", "model": "fake-small", "provider": "fake"}
        )
        assert r.status_code == 400 and r.json()["error"] == dev_only
        # On the scripted model the name must be one of the pack's fixtures; no path leaks.
        for name in ("nope", "../etc/passwd"):
            r = await c.post(
                "/v1/turns", json={"message": "hi", "model": "fake-small", "fixture": name}
            )
            assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
            assert str(ROOT) not in r.text and "fixtures" not in r.json()["error"]["message"]
        r = await c.post(
            "/v1/turns", json={"message": "hi", "model": "fake-small", "fixture": "nope"}
        )
        assert r.json()["error"]["message"] == "no fixture named 'nope'"
    assert seen == []  # nothing reached the hosted model


@pytest.mark.anyio
async def test_auto_with_the_budget_spent_takes_a_fixture(tmp_path: Path) -> None:
    h = public_harness(tmp_path, priced=True, overrides={"public.hosted_budget_usd_per_day": 0.01})
    async with client(h) as c:
        body = {"message": "What are your support hours?", "fixture": "plain_answer"}
        r = await c.post("/v1/turns", json=body)
        assert r.status_code == 400  # Auto runs on the priced alias while the budget lasts
        h.hosted_budget.charge(1.0)
        out = await run(c, h, body)
    assert out["response"].status_code == 202
    assert node_exits(out["events"], "model_layer")[0]["summary"]["model"] == "fake-small"
    assert "9am to 5pm" in out["events"][-1]["summary"]["reply"]


@pytest.mark.anyio
async def test_budget_exhausted_is_a_409_and_byo_bypasses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SITE_KEY_ENV, "site-key-value")
    h = public_harness(
        tmp_path, hosted=True, overrides={"public.hosted_budget_usd_per_day": 0.0001}
    )
    seen = mock_anthropic(h)
    h.hosted_budget.charge(1.0)  # today's budget is spent
    async with client(h) as c:
        pub = (await c.get("/v1/capabilities")).json()["public"]
        by = {m["alias"]: m for m in pub["models"]}
        assert by["hosted-haiku"]["reason"] == "budget_exhausted" and by["fake-small"]["default"]
        assert pub["budget"] == {**pub["budget"], "status": "exhausted", "spent_fraction": 1.0}
        r = await c.post("/v1/turns", json={"message": "hi", "model": "hosted-haiku"})
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "budget_exhausted" and err["resets_at"].endswith("T00:00:00Z")
        # A BYO key pays for itself: accepted, and the shared ledger is not charged.
        before = h.hosted_budget.today()
        byo = {"X-Harnesswalk-Provider-Key": "sk-byo-test-0123456789"}
        out = await run(c, h, {"message": "hi", "model": "hosted-haiku"}, headers=byo)
        assert out["response"].status_code == 202
        assert node_exits(out["events"], "model_layer")[0]["summary"]["model"] == "hosted-haiku"
        assert seen[-1].headers["x-api-key"] == "sk-byo-test-0123456789"
        assert h.hosted_budget.today()["calls"] == before["calls"]
        # The P6 contract's spelling of the header works too.
        alt = {"X-Harnesswalk-Api-Key": "sk-byo-test-alt"}
        out = await run(c, h, {"message": "hi", "model": "hosted-sonnet"}, headers=alt)
        assert (
            out["response"].status_code == 202
            and seen[-1].headers["x-api-key"] == "sk-byo-test-alt"
        )


@pytest.mark.anyio
async def test_mid_turn_exhaustion_falls_through_to_fake_small_with_a_notice(
    tmp_path: Path,
) -> None:
    fx = tmp_path / "fx"
    call = {"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A-1001"}}]}
    call["usage"] = {"input_tokens": 1, "output_tokens": 0}  # one cent on the priced alias
    write_fixture(fx, "two_calls", [[call, {"text": "Order A-1001 has shipped."}]])
    h = public_harness(
        tmp_path, "two_calls", fixtures=fx, priced=True,
        overrides={"public.hosted_budget_usd_per_day": 0.01},
    )  # fmt: skip
    async with client(h) as c:
        out = await run(c, h, {"message": "Where is order A-1001?"})
        caps = (await c.get("/v1/capabilities")).json()["public"]
    events = out["events"]
    assert check_stream(events) == []
    first, second = (e["summary"] for e in node_exits(events, "model_layer"))
    assert first["model"] == "fake-priced" and "notice" not in first
    assert second["model"] == "fake-small"
    assert second["notice"] == "budget_exhausted" and second["downgraded_from"] == "fake-priced"
    assert node_exits(events, "loop")[0]["summary"]["notices"] == ["budget_exhausted"]
    end = events[-1]["summary"]
    assert end["exit"] == "answered" and end["notices"][0]["reason"] == "budget_exhausted"
    assert "00:00 UTC" in end["notices"][0]["message"]
    assert h.hosted_budget.today()["spent_usd"] == 0.01
    assert caps["budget"]["status"] == "exhausted"
    priced = next(m for m in caps["models"] if m["alias"] == "fake-priced")
    assert priced["reason"] == "budget_exhausted"


# -- the ledger (section 4) -------------------------------------------------------------------


def test_ledger_survives_a_restart_and_resets_by_date(tmp_path: Path) -> None:
    from harnesswalk.adapters.storage.sqlite import Database

    now = [1_793_570_000.0]  # 2026-11-01, mid-afternoon UTC
    db = Database(tmp_path / "budgets.db", "")
    ledger = HostedBudget(db, lambda: 15.0, clock=lambda: now[0])
    ledger.charge(10.0)
    ledger.charge(5.5)
    assert not ledger.ok() and ledger.today()["calls"] == 2
    db.close()
    again = HostedBudget(Database(tmp_path / "budgets.db", ""), lambda: 15.0, clock=lambda: now[0])
    assert again.today()["spent_usd"] == 15.5 and again.public_view()["status"] == "exhausted"
    now[0] += 86400  # the next UTC day starts at zero, no cron
    assert again.ok() and again.today()["spent_usd"] == 0 and again.today()["calls"] == 0


@pytest.mark.anyio
async def test_ops_budget_is_dev_only(tmp_path: Path) -> None:
    from tests.unit.support import harness

    dev = harness(tmp_path / "dev")
    dev.hosted_budget.charge(0.25)
    async with client(dev) as c:
        row = (await c.get("/v1/ops/budget")).json()
    assert row["spent_usd"] == 0.25 and row["limit_usd"] == 15 and row["calls"] == 1
    pub = public_harness(tmp_path / "pub")
    async with client(pub) as c:
        r = await c.get("/v1/ops/budget")
    assert r.status_code == 403 and "usd" not in r.text


# -- BYO key hygiene (section 3, section 5 item 7) --------------------------------------------


@pytest.mark.anyio
async def test_byo_key_never_lands_in_any_store_log_or_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    key = "sk-ant-byo-NEVER-STORE-7f3a9c"
    h = public_harness(tmp_path, hosted=True)

    def refuse(request: httpx.Request) -> httpx.Response:  # an error that echoes the key
        return httpx.Response(401, json={"error": {"message": f"bad key {key}"}})

    calls = {"n": 0}

    def answer(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return refuse(request) if calls["n"] == 1 else httpx.Response(200, json={
            "content": [{"type": "text", "text": "fine"}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 2},
        })  # fmt: skip

    seen = mock_anthropic(h, answer)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logs.JsonFormatter())
    logging.getLogger("harnesswalk").addHandler(handler)
    caplog.set_level(logging.DEBUG)
    try:
        app = create_app(h)
        async with client(h, app=app) as c:
            headers = {"X-Harnesswalk-Provider-Key": key}
            failed = await run(c, h, {"message": "hi", "model": "hosted-haiku"}, headers=headers)
            ok = await run(c, h, {"message": "again", "model": "hosted-haiku"}, headers=headers)
            for out in (failed, ok):
                turn = out["response"].json()["turn_id"]
                assert key not in (await c.get(f"/v1/turns/{turn}")).text
                assert key not in (await c.get(f"/v1/turns/{turn}/events")).text
    finally:
        logging.getLogger("harnesswalk").removeHandler(handler)
    assert seen and all(r.headers["x-api-key"] == key for r in seen)  # it did reach the provider
    assert key not in str(failed["events"]) + str(ok["events"])
    assert key not in caplog.text and key not in stream.getvalue()
    for path, data in every_file_bytes(tmp_path / "data"):
        assert key.encode() not in data, path
    assert h.hosted_budget.today()["calls"] == 0  # BYO-paid calls never charge the ledger


# -- P6 review: concurrency, reservations and the per-call BYO waiver -------------------------


def _slow_paid(tmp_path: Path) -> Path:
    fx = tmp_path / "fx"
    reply = {"text": "ok", "latency_ms": 300, "usage": {"input_tokens": 1, "output_tokens": 0}}
    write_fixture(fx, "slow_paid", [[reply]])
    return fx


async def _concurrent_turns(h: Harness, n: int) -> list[httpx.Response]:
    import asyncio

    app = create_app(h)

    async def one(i: int) -> httpx.Response:
        async with client(h, app=app) as c:
            headers = {"X-Forwarded-For": f"2001:db8:{i:x}::1"}  # one /64 each
            return await c.post("/v1/turns", json={"message": "hi"}, headers=headers)

    responses = await asyncio.gather(*(one(i) for i in range(n)))
    for r in responses:
        if r.status_code == 202:
            await h.gateway.wait(r.json()["turn_id"], 20)
    return list(responses)


@pytest.mark.anyio
async def test_concurrent_turns_cannot_overshoot_the_daily_budget(tmp_path: Path) -> None:
    """The review's PoC shape: 0.01 per call, 0.02 a day, 40 turns at once. Every priced call
    reserves its worst case first, so the day ends within one call of the limit; the turns that
    found the budget committed ran on the fake model and say so."""
    overrides = {
        "public.hosted_budget_usd_per_day": 0.02,
        "gateway.trust_proxy": True,
        "public.max_running_hosted_turns": 100,  # the reservation alone, not the turn cap
    }
    fx = _slow_paid(tmp_path)
    h = public_harness(tmp_path, "slow_paid", fixtures=fx, priced=True, overrides=overrides)
    responses = await _concurrent_turns(h, 40)
    assert [r.status_code for r in responses] == [202] * 40
    today = h.hosted_budget.today()
    assert today["spent_usd"] <= today["limit_usd"] + 0.01, today
    assert h.hosted_budget.held_usd() == 0  # every hold settled or released
    notices = [h.gateway.turns.get(r.json()["turn_id"]) for r in responses]
    fell = [s for s in notices if s is not None and s.ctx.notices]
    assert len(fell) >= 38, len(fell)


@pytest.mark.anyio
async def test_running_hosted_turns_are_capped_per_client_and_on_the_site(
    tmp_path: Path,
) -> None:
    overrides = {"gateway.trust_proxy": True, "public.max_running_hosted_turns": 3}
    fx = _slow_paid(tmp_path)
    h = public_harness(tmp_path, "slow_paid", fixtures=fx, priced=True, overrides=overrides)
    responses = await _concurrent_turns(h, 6)
    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 202, 202, 429, 429, 429]
    refused = next(r for r in responses if r.status_code == 429)
    assert refused.headers["Retry-After"] == "10"
    assert refused.json()["error"]["limit"] == "running_turns"
    assert "on the site" in refused.json()["error"]["message"]
    # Per client: two at once from one /64 (two addresses in it), the third is refused; a turn
    # on the fake model is never counted.
    h2 = public_harness(
        tmp_path / "two",
        "slow_paid",
        fixtures=fx,
        priced=True,
        overrides={"gateway.trust_proxy": True},
    )
    app = create_app(h2)
    async with client(h2, app=app) as c:
        import asyncio

        def body(i: int, **extra: str) -> dict[str, str]:
            return {"message": "hi", "session_id": f"sess_{i:020x}", **extra}

        sent = [
            c.post("/v1/turns", json=body(i), headers={"X-Forwarded-For": f"2001:db8:7::{i}"})
            for i in (1, 2, 3)
        ]
        sent.append(
            c.post(
                "/v1/turns",
                json=body(4, model="fake-small"),
                headers={"X-Forwarded-For": "2001:db8:7::4"},
            )
        )
        out = await asyncio.gather(*sent)
        assert sorted(r.status_code for r in out[:3]) == [202, 202, 429]
        assert out[3].status_code == 202  # the fake model is never counted
        refused = next(r for r in out if r.status_code == 429)
        assert "from this address" in refused.json()["error"]["message"]
        for r in out:
            if r.status_code == 202:
                await h2.gateway.wait(r.json()["turn_id"], 20)
    assert h2.gateway._hosted_running == {}  # freed when the turns ended


@pytest.mark.anyio
async def test_a_bogus_byo_key_waives_nothing(tmp_path: Path, monkeypatch) -> None:
    """The review's PoC: the budget spent, a key header that is not a key. The turn's calls,
    the internal ones (compaction) included, go out with that key or not at all: the site key
    is never used past the budget."""
    monkeypatch.setenv(SITE_KEY_ENV, "SITE-KEY")
    h = public_harness(
        tmp_path, hosted=True, overrides={"public.hosted_budget_usd_per_day": 0.0001}
    )
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["x-api-key"]
        keys.append(key)
        if key != "SITE-KEY":
            return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "summary"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3000, "output_tokens": 200},
            },
        )

    mock_anthropic(h, handler)
    h.hosted_budget.charge(1.0)
    assert not h.hosted_budget.ok()
    byo = {"X-Harnesswalk-Provider-Key": "bogus"}
    filler = "please remember this filler text. " * 200
    async with client(h) as c:
        sid = None
        for i in range(4):
            body = {"message": f"turn {i} " + filler, "model": "hosted-haiku"}
            if sid:
                body["session_id"] = sid
            out = await run(c, h, body, headers=byo)
            assert out["response"].status_code == 202, out["response"].text
            sid = out["response"].json()["session_id"]
    assert keys and set(keys) == {"bogus"}, keys
    assert h.hosted_budget.today()["calls"] == 1  # only the test's own charge
    assert h.turn_keys == {}  # every turn dropped its key when it ended
