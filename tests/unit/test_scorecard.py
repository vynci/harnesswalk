"""The eval `model` suite, the pinned router and the model ladder scorecard (P0 of the 0.2.0
line). Offline: the fake provider answers, Ollama is a mock transport, and the scorecard's
aggregation and Markdown are checked from fixture run data."""

import json
from pathlib import Path

import httpx
import pytest

from harnesswalk.config.loader import load_settings
from harnesswalk.execution.providers import ProviderError, ProviderRequest
from harnesswalk.execution.providers.ollama import OllamaProvider
from harnesswalk.execution.router import Router, RouteRequest
from harnesswalk.ops.eval import EvalRequestError
from harnesswalk.ops.eval import scorecard as sc
from harnesswalk.runtime import Harness
from harnesswalk.types import Message
from tests.spec.support import ROOT
from tests.unit.support import settings

# A second scripted alias, priced, so a pinned run is told apart from the default fake-small.
FAKE_ALT = {"provider": "fake", "price_in_per_mtok": 1.0, "price_out_per_mtok": 0.5}


def starter(tmp_path: Path, **overrides) -> Harness:
    extra = {"models.models.fake-alt": FAKE_ALT, **overrides}
    return Harness(settings(tmp_path, "plain_answer", profile=None, overrides=extra))


async def finished(h: Harness, run_id: str, tenant: str = "dev") -> dict:
    await h.evals.offline.wait(run_id)
    run = h.evals.store.run(tenant, run_id)
    run["cases"] = h.evals.store.cases(run_id)
    return run


# -- the model suite ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_model_suite_on_a_fake_alias_equals_golden(tmp_path: Path) -> None:
    h = starter(tmp_path)
    golden = await finished(h, h.evals.offline.start("dev", "_starter", "golden")["run_id"])
    model = await finished(
        h, h.evals.offline.start("dev", "_starter", "model", model="fake-small")["run_id"]
    )
    assert model["suite"] == "model" and model["model"] == "fake-small"
    assert model["totals"] == golden["totals"] and model["totals"]["failed"] == 0
    # Every deterministic scorer runs, and no judge.
    names = {n for c in model["cases"] for n in c["scores"]}
    assert {"path", "tool_calls", "citations", "answer_match", "pii_leak", "schema"} <= names
    assert not names & {"faithfulness", "relevance"}
    assert model["judge_model"] is None
    await h.aclose()


@pytest.mark.anyio
async def test_the_model_suite_pins_every_call_and_records_usage(tmp_path: Path) -> None:
    h = starter(tmp_path)
    run = h.evals.offline.start("dev", "_starter", "model", model="fake-alt")
    done = await finished(h, run["run_id"])
    assert done["totals"]["failed"] == 0
    case = next(c for c in done["cases"] if c["case_id"] == "order_status")
    events = h.tracer.get_turn(done["case_tenant"], case["turn_id"])
    routed = [e["summary"] for e in events if e["type"] == "node_exit" and e["node_id"] == "router"]
    assert routed and all(r["model"] == "fake-alt" and r["alternatives"] == [] for r in routed)
    # The rule still matches by step and risk: the after-tool call is the lookup rule.
    assert [r["rule"] for r in routed] == ["default", "lookup"]
    assert done["cost"]["input_tokens"] > 0 and done["cost"]["usd"] > 0
    await h.aclose()


@pytest.mark.anyio
async def test_model_runs_are_compared_per_model(tmp_path: Path) -> None:
    h = starter(tmp_path)
    first = await finished(
        h, h.evals.offline.start("dev", "_starter", "model", model="fake-small")["run_id"]
    )
    other = await finished(
        h, h.evals.offline.start("dev", "_starter", "model", model="fake-alt")["run_id"]
    )
    again = await finished(
        h, h.evals.offline.start("dev", "_starter", "model", model="fake-small")["run_id"]
    )
    assert other["previous_run_id"] is None  # never compared with another model's run
    assert again["previous_run_id"] == first["run_id"]
    # golden keeps its per (tenant, pack, suite) lookup, whatever model ran before
    gold = await finished(h, h.evals.offline.start("dev", "_starter", "golden")["run_id"])
    assert gold["previous_run_id"] is None
    assert h.evals.store.previous_run("dev", "_starter", "golden")["run_id"] == gold["run_id"]
    await h.aclose()


@pytest.mark.anyio
async def test_model_run_requests_are_checked(tmp_path: Path) -> None:
    h = starter(tmp_path)
    off = h.evals.offline

    def refused(**kwargs) -> tuple[int, str]:
        with pytest.raises(EvalRequestError) as info:
            off.start("dev", "_starter", "model", **kwargs)
        return info.value.status, info.value.code

    assert refused() == (400, "invalid_request")  # the model suite needs a model
    assert refused(model="nope") == (400, "invalid_request")
    assert refused(model="local-embed") == (400, "invalid_request")
    # The harness runs on fake: an Ollama alias has no configured provider here.
    assert refused(model="local-4b") == (422, "provider_not_configured")
    await h.aclose()


def test_the_eval_cli_runs_the_model_suite(tmp_path: Path, monkeypatch, capsys) -> None:
    from harnesswalk.__main__ import main

    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HARNESSWALK_PROFILE", raising=False)
    argv = ["eval", "_starter", "--suite", "model", "--model", "fake-small", "--provider", "fake"]
    assert main([*argv, "--case", "support_hours"]) == 0
    out = capsys.readouterr().out
    assert "PASSED  support_hours" in out and "(model, pack _starter)" in out


# -- the pinned router -------------------------------------------------------------------------


def test_a_pinned_router_answers_on_the_pin_and_keeps_rule_matching() -> None:
    models = {
        "a": {"provider": "p", "price_out_per_mtok": 0},
        "b": {"provider": "p", "price_out_per_mtok": 0},
        "dear": {"provider": "p", "price_out_per_mtok": 9},
        "off": {"provider": "q", "price_out_per_mtok": 0},
    }
    rules = [
        {"id": "lookup", "when": {"step": "after_tool"}, "use": ["a"], "max_price_out_per_mtok": 1},
        {"id": "default", "when": {}, "use": ["a", "b"]},
    ]
    router = Router(rules, models, lambda p: p == "p")
    assert router.route(RouteRequest(1, "first")).model == "a"
    pinned = router.pinned("b")
    first = pinned.route(RouteRequest(1, "first"))
    assert (first.rule, first.model, first.alternatives) == ("default", "b", [])
    assert pinned.route(RouteRequest(2, "after_tool")).rule == "lookup"
    # A rule's price cap still applies to the pinned alias: the next matching rule routes it.
    dear = router.pinned("dear").route(RouteRequest(2, "after_tool"))
    assert (dear.rule, dear.model) == ("default", "dear")
    # Restricting providers keeps the pin, and a pin on an unconfigured provider fails.
    narrowed = router.pinned("b").restricted(lambda p: True)
    assert narrowed.route(RouteRequest(1, "first")).model == "b"
    with pytest.raises(ProviderError):
        router.pinned("off").route(RouteRequest(1, "first"))


# -- HARNESSWALK_OLLAMA_URL and the Ollama adapter --------------------------------------------


def test_the_ollama_url_comes_from_its_base_url_env() -> None:
    def ollama_url(env: dict[str, str]) -> str:
        return load_settings(ROOT, env=env).get("models.providers.ollama.base_url")

    assert ollama_url({}) == "http://localhost:11434"
    assert ollama_url({"HARNESSWALK_OLLAMA_URL": "http://gpu:11434"}) == "http://gpu:11434"
    assert ollama_url({"HARNESSWALK_OLLAMA_URL": ""}) == "http://localhost:11434"
    s = load_settings(ROOT, env={"HARNESSWALK_OLLAMA_URL": "http://gpu:11434"})
    assert OllamaProvider.from_settings(s).base_url == "http://gpu:11434"
    assert s.get("models.providers.ollama.base_url_env") == "HARNESSWALK_OLLAMA_URL"


def test_the_ladder_aliases_are_configured() -> None:
    models = load_settings(ROOT, env={}).get("models.models")
    ladder = {"local-2b": "qwen3.5:2b", "local-4b": "qwen3.5:4b", "local-9b": "qwen3.5:9b"}
    ladder["local-e4b"] = "gemma4:e4b"
    for alias, name in ladder.items():
        cfg = models[alias]
        assert (cfg["provider"], cfg["name"], cfg["tools"]) == ("ollama", name, True)
        assert cfg["context_tokens"] == 32000 and cfg["price_out_per_mtok"] == 0
    assert models["local-8b"]["name"] == "llama3.1:8b" and "local-small" in models


@pytest.mark.anyio
async def test_the_ollama_adapter_sends_alias_params() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}, "done_reason": "stop"})

    p = OllamaProvider("http://ollama:11434", transport=httpx.MockTransport(handler))
    await p.complete(
        ProviderRequest(
            model="qwen3.5:4b",
            messages=[Message("user", "hi")],
            tools=[],
            max_output_tokens=64,
            params={"think": False, "options": {"temperature": 0, "num_predict": 9}},
        )
    )
    assert seen[0]["think"] is False
    assert seen[0]["options"] == {"temperature": 0, "num_predict": 64}  # the layer's cap wins
    # No context_tokens: no num_ctx, Ollama's own default (backward compatible).
    assert "num_ctx" not in seen[0]["options"]
    await p.complete(
        ProviderRequest(
            model="qwen3.5:4b", messages=[Message("user", "hi")], tools=[], context_tokens=32000
        )
    )
    assert seen[1]["options"]["num_ctx"] == 32000


@pytest.mark.anyio
async def test_the_model_layer_asks_ollama_for_the_alias_context(tmp_path: Path) -> None:
    from harnesswalk.control.gateway import TurnRequest
    from harnesswalk.types import Identity

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": "9am to 5pm"}})
        return httpx.Response(200, json={"models": []})

    h = Harness(settings(tmp_path, "plain_answer", profile=None))
    h._turn_providers["ollama"] = OllamaProvider(
        "http://ollama:11434", transport=httpx.MockTransport(handler)
    )
    h.provider_available = lambda name: True  # the test stands in for a reachable Ollama
    state = await h.gateway.start_turn(
        TurnRequest(
            message="What are your support hours?",
            identity=Identity("dev", None, ["user"], anonymous=False),
            provider="ollama",
            pin_model="local-4b",
            internal=True,
        )
    )
    await h.gateway.wait(state.ctx.turn_id, timeout=30)
    assert seen and seen[0]["model"] == "qwen3.5:4b"
    assert seen[0]["options"]["num_ctx"] == 32000 and seen[0]["think"] is False
    await h.aclose()


# -- probing Ollama ----------------------------------------------------------------------------


def ollama_mock(tools: bool = True, chat_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.1"})
        if path == "/api/tags":
            details = {"parameter_size": "4.2B", "quantization_level": "Q4_K_M"}
            tag = {"name": "qwen3.5:4b", "digest": "d8b0f5e9760c1234", "details": details}
            return httpx.Response(200, json={"models": [tag]})
        if path == "/api/show":
            caps = ["completion", "tools"] if tools else ["completion"]
            return httpx.Response(200, json={"capabilities": caps, "details": {"family": "qwen35"}})
        if path == "/api/chat":
            body = "model does not support tools" if chat_status == 400 else "{}"
            return httpx.Response(chat_status, text=body)
        if path == "/api/generate":
            sent = json.loads(request.content)
            return httpx.Response(200 if sent.get("keep_alive") == 0 else 400, json={})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_probing_ollama_finds_models_capabilities_and_tool_support() -> None:
    url = "http://gpu:11434"
    probe = await sc.probe_ollama(url, ["qwen3.5:4b", "qwen3.5:2b"], transport=ollama_mock())
    assert probe["reachable"] and probe["version"] == "0.34.1"
    assert probe["models"]["qwen3.5:4b"]["capabilities"] == ["completion", "tools"]
    assert probe["models"]["qwen3.5:2b"] == {"present": False}
    assert await sc.preflight_ollama(url, "qwen3.5:4b", transport=ollama_mock()) is None
    assert await sc.unload_ollama(url, "qwen3.5:4b", transport=ollama_mock()) is True
    no_tools = ollama_mock(chat_status=400)
    assert await sc.preflight_ollama(url, "x", transport=no_tools) == "no tool support"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    gone = await sc.probe_ollama(url, ["qwen3.5:4b"], transport=httpx.MockTransport(down))
    assert not gone["reachable"] and gone["error"] == "ConnectError"


def test_availability_names_why_a_model_cannot_run() -> None:
    s = load_settings(ROOT, env={})
    models = s.get("models.models")
    up = {
        "host": "http://gpu:11434",
        "reachable": True,
        "models": {
            "qwen3.5:4b": {"present": True, "capabilities": ["completion", "tools"]},
            "qwen3.5:9b": {"present": True, "capabilities": ["completion"]},
            "qwen3.5:2b": {"present": False},
        },
    }
    assert sc.availability("fake-small", models["fake-small"], s, None) is None
    assert sc.availability("local-4b", models["local-4b"], s, up) is None
    assert sc.availability("local-9b", models["local-9b"], s, up) == "no tool support"
    assert "not on the Ollama server" in sc.availability("local-2b", models["local-2b"], s, up)
    down = {"host": "http://gpu:11434", "reachable": False}
    assert (
        sc.availability("local-4b", models["local-4b"], s, down) == "no Ollama at http://gpu:11434"
    )
    no_key = sc.availability("hosted-haiku", models["hosted-haiku"], s, None, env={})
    assert no_key == "provider anthropic is not configured (no ANTHROPIC_API_KEY)"
    keyed = {"ANTHROPIC_API_KEY": "sk-test"}
    assert sc.availability("hosted-haiku", models["hosted-haiku"], s, None, env=keyed) is None


# -- aggregation and Markdown ------------------------------------------------------------------


def case(case_id: str, status: str = "passed", failed=(), errors=()) -> dict:
    return {"case_id": case_id, "status": status, "failed": list(failed), "errors": list(errors)}


def cell(pack: str, model: str, cases: list[dict], usd: float = 0.0) -> dict:
    passed = sum(c["status"] == "passed" for c in cases)
    skipped = sum(c["status"] == "skipped" for c in cases)
    return {
        "pack": pack,
        "model": model,
        "status": "ok",
        "reason": None,
        "run_id": f"run_{pack}_{model}",
        "suite": "model",
        "totals": {
            "cases": len(cases),
            "passed": passed,
            "failed": len(cases) - passed - skipped,
            "skipped": skipped,
        },
        "cost": {"input_tokens": 1000, "output_tokens": 100, "usd": usd},
        "seconds": 12.0,
        "cases": cases,
    }


def fixture_data() -> dict:
    cells = [
        cell("_starter", "fake-small", [case("a"), case("b"), case("c")]),
        cell("sports", "fake-small", [case("x"), case("y", "skipped")]),
        cell(
            "_starter",
            "local-4b",
            [
                case("a", "failed", ["tool_calls", "answer_match"]),
                case("b", "failed", ["answer_match"]),
                case("c"),
            ],
        ),
        cell(
            "sports",
            "local-4b",
            [case("x", "error", errors=["provider_timeout"]), case("y", "skipped")],
        ),
        cell(
            "_starter",
            "hosted-haiku",
            [case("a", "failed", ["citations"]), case("b"), case("c")],
            usd=0.0123,
        ),  # fmt: skip
        sc.not_run("sports", "hosted-haiku", "spend cap $5.00 reached"),
        sc.not_run("_starter", "local-2b", "qwen3.5:2b is not on the Ollama server"),
        sc.not_run("sports", "local-2b", "qwen3.5:2b is not on the Ollama server"),
    ]
    models = [
        {"alias": "fake-small", "provider": "fake", "name": "fake-small", "details": {}},
        {
            "alias": "local-4b",
            "provider": "ollama",
            "name": "qwen3.5:4b",
            "details": {"parameter_size": "4.2B", "quantization_level": "Q4_K_M"},
        },
        {"alias": "local-2b", "provider": "ollama", "name": "qwen3.5:2b", "details": {}},
        {"alias": "hosted-haiku", "provider": "anthropic", "name": "claude-haiku-5-5"},
    ]
    return sc.scorecard_data(
        cells=cells,
        models=models,
        packs=["_starter", "sports"],
        tenant="scorecard",
        harness_version="0.1.1",
        spec_version="0.1.0",
        ollama={"host": "http://gpu:11434", "version": "0.34.1", "reachable": True},
        command="uv run harnesswalk scorecard",
    )


def test_the_summary_counts_failures_by_scorer_and_common_cases() -> None:
    stats = sc.summarize(fixture_data())
    four = stats["local-4b"]
    assert (four["passed"], four["scored"], four["skipped"], four["failed"]) == (1, 4, 1, 3)
    assert four["by_scorer"]["tool_calls"] == 1 and four["by_scorer"]["answer_match"] == 2
    assert four["by_scorer"]["error"] == 1 and four["answer_match_only"] == 1
    # `_starter/a` fails on two models, so it is the most common failure.
    assert four["common_failures"][0] == {"case": "_starter/a", "models_failing": 2}
    assert stats["fake-small"]["failed"] == 0 and stats["fake-small"]["scored"] == 4
    assert stats["local-2b"]["ran"] == 0
    haiku = stats["hosted-haiku"]
    assert haiku["ran"] == 1 and haiku["usd"] == 0.0123
    assert haiku["reasons"] == ["spend cap $5.00 reached"]


def test_the_markdown_has_the_matrix_and_the_per_model_breakdown() -> None:
    md = sc.render_markdown(fixture_data())
    assert md.startswith("# Model ladder scorecard\n")
    assert "Ollama: http://gpu:11434 (version 0.34.1)" in md
    assert "| `local-4b` | ollama | `qwen3.5:4b` | 4.2B | Q4_K_M |" in md
    assert "| Pack | `fake-small` | `local-4b` | `local-2b` | `hosted-haiku` |" in md
    assert "| `_starter` | 3/3 | 1/3 | n/a | 2/3 |" in md
    assert "| `sports` | 1/1 | 0/1 | n/a | n/a |" in md
    assert "| **All packs** | **4/4** (100%) | **1/4** (25%) | n/a | **2/3** (67%) |" in md
    assert "Not run: qwen3.5:2b is not on the Ollama server." in md
    assert "Some cells did not run: spend cap $5.00 reached." in md
    assert "| 0 | 1 | 0 | 2 | 0 | 0 | 0 | 1 |" in md  # local-4b by scorer
    assert "`_starter/a` (2 of 3 models)" in md
    assert "cost $0.0123" in md
    assert "—" not in md  # house style: no em-dashes


@pytest.mark.anyio
async def test_the_matrix_records_n_a_and_never_aborts() -> None:
    ran: list[tuple[str, str]] = []

    async def run_cell(pack: str, alias: str) -> dict:
        ran.append((pack, alias))
        if alias == "boom":
            raise RuntimeError("the harness fell over")
        return cell(pack, alias, [case("a")])

    async def prepare(alias: str) -> str | None:
        return "no tool support" if alias == "notools" else None

    unloaded: list[str] = []

    async def finish(alias: str) -> None:
        unloaded.append(alias)
        if alias == "boom":
            raise RuntimeError("unload failed")

    reasons = {"fake-small": None, "missing": "gone", "notools": None, "boom": None}
    cells = await sc.run_matrix(
        ["p1", "p2"], ["fake-small", "missing", "notools", "boom"], run_cell, reasons,
        prepare=prepare, finish=finish,
    )  # fmt: skip
    by = {(c["pack"], c["model"]): c for c in cells}
    assert len(cells) == 8
    assert by[("p1", "missing")] == sc.not_run("p1", "missing", "gone")
    assert by[("p2", "notools")]["reason"] == "no tool support"
    assert by[("p1", "boom")]["status"] == "error" and "fell over" in by[("p1", "boom")]["reason"]
    assert by[("p2", "fake-small")]["status"] == "ok"
    assert ("p1", "missing") not in ran and ("p1", "notools") not in ran
    assert unloaded == ["fake-small", "boom"]  # after each model that ran, and never fatal


def test_the_scorecard_cli_writes_markdown_and_json(tmp_path: Path, monkeypatch, capsys) -> None:
    from harnesswalk.__main__ import main

    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HARNESSWALK_PROFILE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out, data = tmp_path / "scorecard.md", tmp_path / "scorecard.json"
    argv = ["scorecard", "--packs", "_starter", "--models", "fake-small,hosted-haiku"]
    assert main([*argv, "--out", str(out), "--json", str(data)]) == 0
    md = out.read_text(encoding="utf-8")
    assert "| `_starter` | 16/16 | n/a |" in md
    saved = json.loads(data.read_text(encoding="utf-8"))
    assert saved["tenant"] == "scorecard" and saved["ollama"] is None
    fake = next(c for c in saved["cells"] if c["model"] == "fake-small")
    assert fake["suite"] == "model" and fake["totals"]["failed"] == 0
    assert "fake-small     16/16" in capsys.readouterr().out
    assert main(["scorecard", "--packs", "nope", "--out", str(out)]) == 2
    assert main(["scorecard", "--models", "nope", "--out", str(out)]) == 2
