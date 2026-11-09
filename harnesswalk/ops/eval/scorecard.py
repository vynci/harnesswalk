"""The model ladder scorecard: a pack x model pass-count matrix from real eval runs.

Contract: spec/contracts/evaluator.md ("Model suite and the scorecard"). Not a node: the
`harnesswalk scorecard` command (harnesswalk/__main__.py) drives it, and `docs/scorecard.md`
is its output.

Each cell is one `model`-suite eval run (harnesswalk/ops/eval/offline.py): the pack's golden
cases answered by one model alias, pinned for every call of each case turn, and judged by the
deterministic scorers only. Cells run one at a time, so one Ollama model is loaded at a time
(unloading is Ollama's job). A cell that cannot run (its provider is not configured or not
reachable, its model is not on the Ollama server, the model takes no tools) is `n/a` with the
reason and never stops the matrix.

The module has three parts, so the aggregation and the Markdown are testable from fixture
data without a model: probing (`probe_ollama`, `availability`), reducing a finished run to a
cell (`cell_of`), and summarizing and rendering (`summarize`, `render_markdown`).
"""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from harnesswalk.events import iso

# The failure columns, in the order the Markdown prints them. `error` counts cases whose turn
# raised an error (a provider error, a timeout) or that could not be scored.
SCORER_COLUMNS = (
    "path",
    "tool_calls",
    "citations",
    "answer_match",
    "checks",
    "schema",
    "pii_leak",
    "error",
)
PROBE_TIMEOUT_S = 10.0
LOAD_TIMEOUT_S = 300.0  # the preflight call loads the model on the server


# -- probing -----------------------------------------------------------------------------------


async def probe_ollama(
    base_url: str,
    names: list[str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """What the Ollama server at `base_url` has: version, and per model name whether it is
    pulled, its capabilities and its details (parameter size, quantization, family)."""
    out: dict[str, Any] = {
        "host": base_url,
        "version": None,
        "reachable": False,
        "error": None,
        "models": {},
    }
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, transport=transport) as client:
            version = await client.get(f"{base_url}/api/version")
            version.raise_for_status()
            out["version"] = version.json().get("version")
            tags = await client.get(f"{base_url}/api/tags")
            tags.raise_for_status()
            pulled = {m.get("name") or m.get("model"): m for m in tags.json().get("models") or []}
            out["reachable"] = True
            for name in names:
                tag = pulled.get(name) or pulled.get(f"{name}:latest")
                info: dict[str, Any] = {"present": tag is not None}
                if tag is not None:
                    info["digest"] = (tag.get("digest") or "")[:12] or None
                    info["details"] = _details(tag.get("details") or {})
                    show = await client.post(f"{base_url}/api/show", json={"model": name})
                    if show.status_code < 400:
                        data = show.json()
                        info["capabilities"] = data.get("capabilities")
                        info["details"] = _details(data.get("details") or {}) or info["details"]
                out["models"][name] = info
    except (httpx.HTTPError, ValueError) as exc:
        out["error"] = f"{type(exc).__name__}"
    return out


def _details(details: dict[str, Any]) -> dict[str, Any]:
    keep = ("family", "parameter_size", "quantization_level", "format")
    return {k: details[k] for k in keep if details.get(k)}


async def preflight_ollama(
    base_url: str,
    name: str,
    params: dict[str, Any] | None = None,
    *,
    context_tokens: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """One tiny `/api/chat` call with a tool: loads the model (with the alias's context window,
    as the cells will) and finds a model that takes no tools (HTTP 400). Returns the `n/a`
    reason, or None when the model answered."""
    body: dict[str, Any] = {
        "model": name,
        "stream": False,
        "messages": [{"role": "user", "content": "Say ok."}],
        "options": {"num_predict": 1, **({"num_ctx": context_tokens} if context_tokens else {})},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "Does nothing.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    }
    for key, value in (params or {}).items():
        if key != "options":
            body.setdefault(key, value)
    try:
        async with httpx.AsyncClient(timeout=LOAD_TIMEOUT_S, transport=transport) as client:
            response = await client.post(f"{base_url}/api/chat", json=body)
    except httpx.HTTPError as exc:
        return f"the model did not answer ({type(exc).__name__})"
    if response.status_code == 400 and "tool" in response.text.lower():
        return "no tool support"
    if response.status_code >= 400:
        return f"the model did not load (HTTP {response.status_code})"
    return None


async def unload_ollama(
    base_url: str, name: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> bool:
    """Unload a model from the Ollama server (`keep_alive: 0`), so the next model's cells do not
    compete with it for VRAM. Never raises; True when the server said yes."""
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S * 3, transport=transport) as client:
            response = await client.post(
                f"{base_url}/api/generate", json={"model": name, "keep_alive": 0}
            )
    except httpx.HTTPError:
        return False
    return response.status_code < 400


def availability(
    alias: str,
    cfg: dict[str, Any] | None,
    settings: Any,
    ollama: dict[str, Any] | None,
    env: Any = None,
) -> str | None:
    """Why a model alias cannot answer here (the `n/a` reason), or None when it can."""
    env = os.environ if env is None else env
    if cfg is None or cfg.get("embedding"):
        return f"unknown model alias {alias}"
    provider = cfg.get("provider", "")
    providers = settings.get("models.providers", {}) or {}
    if provider == "fake":
        return None
    if provider not in providers:
        return f"provider {provider} is not configured"
    pcfg = providers[provider] or {}
    if provider == "ollama":
        if ollama is None or not ollama.get("reachable"):
            host = (ollama or {}).get("host") or pcfg.get("base_url")
            return f"no Ollama at {host}"
        info = (ollama.get("models") or {}).get(cfg.get("name", alias)) or {}
        if not info.get("present"):
            return f"{cfg.get('name', alias)} is not on the Ollama server"
        caps = info.get("capabilities")
        if cfg.get("tools", True) and caps is not None and "tools" not in caps:
            return "no tool support"
        return None
    if provider == "anthropic":
        key = pcfg.get("api_key_env", "ANTHROPIC_API_KEY")
        return None if env.get(key) else f"provider anthropic is not configured (no {key})"
    if provider == "openai_compatible":
        base = pcfg.get("base_url") or env.get(pcfg.get("base_url_env", "") or "", "")
        return None if base else "provider openai_compatible is not configured (no base URL)"
    return f"provider {provider} is not supported by the scorecard"


# -- one cell ----------------------------------------------------------------------------------


def not_run(pack: str, alias: str, reason: str) -> dict[str, Any]:
    return {"pack": pack, "model": alias, "status": "n/a", "reason": reason}


# The `tool_calls` split of a failed case: why the calls did not grade (the page's table).
ANTHROPIC_TEMPERATURE = "provider default (the API rejects temperature)"
STRETCHED_PROVIDERS = ("anthropic", "openai_compatible")


def temperature_of(alias: str, cfg: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Temperature 0 where the provider takes it: the overrides and what the page says.

    Ollama takes `options.temperature`. Anthropic gets none: the 5.5 models answer HTTP 400
    "temperature is deprecated for this model" whenever the body carries one (2026-11-09)."""
    provider = cfg.get("provider")
    if provider == "ollama":
        return {f"models.models.{alias}.params.options.temperature": 0}, "0"
    if provider == "anthropic":
        return {}, ANTHROPIC_TEMPERATURE
    if provider == "fake":
        return {}, "n/a (scripted)"
    return {}, "provider default"


def call_timeouts(
    settings: Any, providers: set[str], case_timeout_s: float, user_set: set[str]
) -> tuple[dict[str, Any], dict[str, float]]:
    """A model call may take as long as the case turn: the hosted providers' HTTP timeout
    (`models.providers.<name>.timeout_s`) and the eval call limit (`evals.model_timeout_s`)
    are raised to at least the case timeout, unless the user `--set` that key. Returns the
    overrides and the provider timeouts the page reports."""
    overrides: dict[str, Any] = {}
    shown: dict[str, float] = {}
    for name in sorted(providers & set(STRETCHED_PROVIDERS)):
        key = f"models.providers.{name}.timeout_s"
        value = float(settings.get(key, 60) or 60)
        if key not in user_set and value < case_timeout_s:
            value = float(case_timeout_s)
            overrides[key] = value
        shown[name] = value
    key = "evals.model_timeout_s"
    if key not in user_set and float(settings.get(key, 180) or 180) < case_timeout_s:
        overrides[key] = float(case_timeout_s)
    return overrides, shown


TOOL_SPLIT = (
    "called when none expected",
    "none called",
    "write tool not called",
    "read tool not called",
    "extra or wrong tool",
)


def tool_split(
    expected: list[str] | None, actual: list[str], read_tools: frozenset[str] | set[str]
) -> str:
    """Why a failed `tool_calls` score failed, as one of TOOL_SPLIT."""
    expected = list(expected or [])
    if not expected:
        return "called when none expected"
    if not actual:
        return "none called"
    missing, at = [], 0
    for name in expected:  # each expected call, found in order after the previous one
        try:
            at = actual.index(name, at) + 1
        except ValueError:
            missing.append(name)
    if any(name not in read_tools for name in missing):
        return "write tool not called"
    if missing:
        return "read tool not called"
    return "extra or wrong tool"


def reduce_case(c: dict[str, Any], read_tools: frozenset[str] | set[str]) -> dict[str, Any]:
    """One stored case result as a scorecard case: status, failing scorers, error codes, and
    the `tool_calls` split when that scorer failed."""
    scores = c.get("scores") or {}
    failing = [name for name, s in scores.items() if not s.get("pass")]
    errors = [e.get("code") for e in (c.get("actual") or {}).get("errors") or []]
    if c.get("error"):
        errors.append(c["error"].get("code"))
    out: dict[str, Any] = {
        "case_id": c["case_id"], "status": c["status"], "failed": failing,
        "errors": [e for e in errors if e],
    }  # fmt: skip
    if "tool_calls" in failing:
        expected = (c.get("expected") or {}).get("tool_calls")
        actual = (c.get("actual") or {}).get("tool_calls") or []
        out["tool_split"] = tool_split(expected, actual, read_tools)
    return out


def sample_of(
    summary: dict[str, Any], cases: list[dict[str, Any]], read_tools: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """One finished eval run as a scorecard sample: run id, totals, cost, time, cases."""
    seconds = None
    if summary.get("started_at") and summary.get("ended_at"):
        seconds = round(_epoch(summary["ended_at"]) - _epoch(summary["started_at"]), 1)
    return {
        "run_id": summary.get("run_id"),
        "status": summary.get("status"),
        "reason": (summary.get("error") or {}).get("message"),
        "totals": summary.get("totals"),
        "cost": summary.get("cost"),
        "seconds": seconds,
        "cases": [reduce_case(c, read_tools) for c in cases],
    }


def cell_of(
    pack: str,
    alias: str,
    summary: dict[str, Any] | None = None,
    cases: list[dict[str, Any]] | None = None,
    *,
    samples: list[dict[str, Any]] | None = None,
    read_tools: frozenset[str] = frozenset(),
    **extra: Any,
) -> dict[str, Any]:
    """A scorecard cell: one or more samples (eval runs) of a (pack, model). `ok` when every
    sample finished, else `error` with the first reason."""
    if samples is None:
        samples = [sample_of(summary or {}, cases or [], read_tools)]
    bad = [x for x in samples if x.get("status") != "done"]
    return {
        "pack": pack,
        "model": alias,
        "status": "error" if bad else "ok",
        "reason": bad[0].get("reason") if bad else None,
        "suite": "model",
        "samples": samples,
        **extra,
    }


def samples_of(cell: dict[str, Any]) -> list[dict[str, Any]]:
    """A cell's samples; a 2026-11-08 cell (one run, no `samples`) is its own sample."""
    return cell.get("samples") or [cell]


def regrade_case(
    case: dict[str, Any],
    read_tools: frozenset[str] | set[str],
    *,
    before_text: str | None = None,
) -> dict[str, Any]:
    """A stored case result graded again under model grading, offline: `tool_calls` and
    `answer_match` from the stored expectation and actual calls and reply, and `schema` from
    the answer before the output guardrails (`before_text`, from the trace) with the JSON
    extraction. Every other score is kept as stored. Returns the case with new scores and
    status."""
    from harnesswalk.execution.output_guardrails import extract_json
    from harnesswalk.ops.eval import scorers

    out = {**case, "scores": dict(case.get("scores") or {})}
    expected = case.get("expected") or {}
    actual = case.get("actual") or {}
    if case.get("status") == "error" or not actual:
        return out
    scores = out["scores"]
    if "tool_calls" in scores and "tool_calls" in expected:
        mode = scorers.tool_calls_mode(expected, "model")
        scores["tool_calls"] = scorers.score_tool_calls(
            expected["tool_calls"], actual.get("tool_calls") or [], mode, read_tools
        )
    reply = actual.get("reply")
    if "schema" in scores and not scores["schema"]["pass"] and before_text is not None:
        schema = expected.get("output_schema")
        found = extract_json(before_text, schema) if schema else None
        if found is not None:
            reply = found
            scores["schema"] = scorers.score_schema(schema, found)
    if "answer_match" in scores:
        scores["answer_match"] = scorers.score_answer(expected, reply, "model")
    out["status"] = "passed" if all(s["pass"] for s in scores.values()) else "failed"
    return out


def regraded_totals(cases: list[dict[str, Any]], before: dict[str, Any]) -> dict[str, Any]:
    totals = {"cases": int(before.get("cases", len(cases))), "passed": 0, "failed": 0}
    totals["skipped"] = sum(c["status"] == "skipped" for c in cases)
    totals["passed"] = sum(c["status"] == "passed" for c in cases)
    totals["failed"] = totals["cases"] - totals["passed"] - totals["skipped"]
    return totals


def _epoch(stamp: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


# -- the whole matrix --------------------------------------------------------------------------


RunCell = Callable[[str, str], Awaitable[dict[str, Any]]]


async def run_matrix(
    packs: list[str],
    models: list[str],
    run_cell: RunCell,
    reasons: dict[str, str | None],
    *,
    prepare: Callable[[str], Awaitable[str | None]] | None = None,
    finish: Callable[[str], Awaitable[Any]] | None = None,
    log: Callable[[str], None] = lambda _line: None,
) -> list[dict[str, Any]]:
    """Every (pack, model) cell, model by model (one Ollama model loaded at a time). A model
    with a reason in `reasons`, or one `prepare(alias)` returns a reason for (it runs once,
    before the model's first cell: the Ollama preflight), is `n/a` in every pack; a cell that
    raises is `error`. `finish(alias)` runs after a model's last cell (the Ollama unload)."""
    cells = []
    for alias in models:
        if not reasons.get(alias) and prepare is not None:
            try:
                reasons[alias] = await prepare(alias)
            except Exception as exc:
                reasons[alias] = f"{type(exc).__name__}: {exc}"[:300]
        for pack in packs:
            reason = reasons.get(alias)
            if reason:
                cells.append(not_run(pack, alias, reason))
                log(f"{alias:<14} {pack:<12} n/a: {reason}")
                continue
            started = time.monotonic()
            try:
                cell = await run_cell(pack, alias)
            except Exception as exc:  # one broken cell never stops the matrix
                cell = {
                    "pack": pack, "model": alias, "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}"[:300],
                }  # fmt: skip
            cells.append(cell)
            took = time.monotonic() - started
            if cell["status"] == "ok":
                log(f"{alias:<14} {pack:<12} {_cell_text(cell)} in {took:.0f}s")
            else:
                log(f"{alias:<14} {pack:<12} {cell['status']}: {cell.get('reason')}")
        if finish is not None and not reasons.get(alias):
            try:
                await finish(alias)
            except Exception:  # an unload that fails never stops the matrix
                log(f"{alias:<14} could not be unloaded")
    return cells


def _scored(totals: dict[str, Any]) -> int:
    return int(totals.get("cases", 0)) - int(totals.get("skipped", 0))


def _num(x: float) -> str:
    return str(int(x)) if float(x) == int(x) else f"{x:.1f}"


def cell_counts(cell: dict[str, Any]) -> tuple[list[int], int]:
    """Per sample the passed count, and the scored count (the same in every sample)."""
    samples = samples_of(cell)
    return [int((x.get("totals") or {}).get("passed", 0)) for x in samples], _scored(
        samples[0].get("totals") or {}
    )


def _cell_text(cell: dict[str, Any] | None) -> str:
    if cell is None:
        return ""
    if cell["status"] == "n/a":
        return "n/a"
    if cell["status"] != "ok":
        return "error"
    passed, scored = cell_counts(cell)
    mean = sum(passed) / len(passed)
    text = f"{_num(mean)}/{scored}"
    if min(passed) != max(passed):
        text += f" ({min(passed)}-{max(passed)})"
    return text + ("¹" if cell.get("carried") else "")


def failing(cell: dict[str, Any]) -> set[str]:
    """The case ids a cell fails: failed or error in at least half its samples."""
    samples = samples_of(cell)
    counts: Counter[str] = Counter()
    for x in samples:
        for c in x.get("cases") or []:
            if c["status"] in ("failed", "error"):
                counts[c["case_id"]] += 1
    return {cid for cid, n in counts.items() if 2 * n >= len(samples)}


def _scorable(cell: dict[str, Any]) -> set[str]:
    return {
        c["case_id"] for c in (samples_of(cell)[0].get("cases") or []) if c["status"] != "skipped"
    }


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    """Per model: mean, min and max pass counts over the samples, failures by scorer and the
    `tool_calls` split (means per sample), the cases this model fails that at least half the
    other real models pass, tokens and cost. `data` is the scorecard JSON."""
    cells = data["cells"]
    aliases = [m["alias"] for m in data["models"]]
    real = {m["alias"] for m in data["models"] if m.get("provider") != "fake"}
    by_cell = {(c["pack"], c["model"]): c for c in cells if c["status"] == "ok"}
    out: dict[str, Any] = {}
    for alias in aliases:
        mine = [c for c in cells if c["model"] == alias]
        ran = [c for c in mine if c["status"] == "ok"]
        n_samples = max((len(samples_of(c)) for c in ran), default=1)
        per_sample = [0] * n_samples
        scored = skipped = 0
        by_scorer: Counter[str] = Counter()
        split: Counter[str] = Counter()
        wording_only = failed = 0.0
        tokens_in = tokens_out = 0
        usd = seconds = 0.0
        outliers: list[dict[str, Any]] = []
        others = [a for a in aliases if a != alias and a in real]
        for cell in ran:
            samples = samples_of(cell)
            passed, cell_scored = cell_counts(cell)
            for i in range(n_samples):
                per_sample[i] += passed[i] if i < len(passed) else passed[-1]
            scored += cell_scored
            skipped += int((samples[0].get("totals") or {}).get("skipped", 0))
            for x in samples:
                cost = x.get("cost") or {}
                tokens_in += int(cost.get("input_tokens", 0))
                tokens_out += int(cost.get("output_tokens", 0))
                usd += float(cost.get("usd", 0.0))
                seconds += float(x.get("seconds") or 0.0)
                for c in x.get("cases") or []:
                    if c["status"] not in ("failed", "error"):
                        continue
                    failed += 1 / len(samples)
                    for name in c.get("failed") or []:
                        by_scorer[name] += 1 / len(samples)
                    if c["status"] == "error" or c.get("errors"):
                        by_scorer["error"] += 1 / len(samples)
                    elif (c.get("failed") or []) == ["answer_match"]:
                        wording_only += 1 / len(samples)
                    if c.get("tool_split"):
                        split[c["tool_split"]] += 1 / len(samples)
            # Cases this model fails where at least half the other real models pass.
            peers = [by_cell[(cell["pack"], a)] for a in others if (cell["pack"], a) in by_cell]
            for case_id in sorted(failing(cell)):
                judged = [p for p in peers if case_id in _scorable(p)]
                passing = [p for p in judged if case_id not in failing(p)]
                if judged and 2 * len(passing) >= len(judged):
                    outliers.append({
                        "case": f"{cell['pack']}/{case_id}", "others_passing": len(passing),
                        "others": len(judged),
                    })  # fmt: skip
        outliers.sort(key=lambda o: -o["others_passing"] / o["others"])
        out[alias] = {
            "ran": len(ran),
            "cells": len(mine),
            "samples": n_samples,
            "passed": sum(per_sample) / n_samples if ran else 0,
            "passed_min": min(per_sample) if ran else 0,
            "passed_max": max(per_sample) if ran else 0,
            "scored": scored,
            "skipped": skipped,
            "failed": round(failed, 2),
            "by_scorer": {name: round(by_scorer.get(name, 0), 2) for name in SCORER_COLUMNS},
            "tool_split": {name: round(split.get(name, 0), 2) for name in TOOL_SPLIT},
            "answer_match_only": round(wording_only, 2),
            "outliers": outliers,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "usd": round(usd, 6),
            "seconds": round(seconds, 1),
            "carried": any(c.get("carried") for c in ran),
            "reasons": sorted({c.get("reason") or "" for c in mine if c["status"] != "ok"}),
        }
    return out


# -- Markdown ----------------------------------------------------------------------------------


def _model_name(m: dict[str, Any]) -> str:
    return m.get("name") or m["alias"]


def _total_text(s: dict[str, Any]) -> str:
    if not s["ran"]:
        return "n/a"
    pct = 100.0 * s["passed"] / s["scored"] if s["scored"] else 0.0
    text = f"**{_num(s['passed'])}/{s['scored']}** ({pct:.0f}%)"
    if s["passed_min"] != s["passed_max"]:
        text += f" ({s['passed_min']}-{s['passed_max']})"
    return text


def render_markdown(data: dict[str, Any]) -> str:
    """docs/scorecard.md from the scorecard JSON."""
    stats = summarize(data)
    models = data["models"]
    aliases = [m["alias"] for m in models]
    lookup = {(c["pack"], c["model"]): c for c in data["cells"]}
    ollama = data.get("ollama")
    grading = data.get("grading", "exact")
    samples = max((stats[a]["samples"] for a in aliases if stats[a]["ran"]), default=1)
    lines = [
        "# Model ladder scorecard",
        "",
        "Which model passes which pack's golden set when the model really answers. Each cell is",
        "a `model`-suite eval run: every golden case of the pack, answered by the model alias",
        "(pinned for every call of the case turn) and judged by the deterministic scorers only",
        "(path, tool calls, citations, answer match, checks, schema, PII leak). No LLM judge,",
        "so a cell is reproducible from the trace. `fake-small` answers from the case fixtures,",
        "so its column is the golden suite itself and every pack should be green there.",
        "",
        f"- Generated: {data['generated_at']} (UTC) by `harnesswalk scorecard`",
        f"- Harness: Harnesswalk {data['harness_version']}, spec {data['spec_version']}",
    ]
    if ollama:
        state = f"version {ollama['version']}" if ollama.get("reachable") else "not reachable"
        lines.append(f"- Ollama: {ollama['host']} ({state})")
    lines += [
        f"- Tenant: `{data['tenant']}`; embeddings: {data.get('embeddings', 'fake')};"
        f" case timeout: {data.get('case_timeout_s', 60)} s"
        + (
            "; provider call timeout: "
            + ", ".join(f"{k} {v:g} s" for k, v in sorted(data["provider_timeouts"].items()))
            + " (raised to the case timeout unless `--set`)"
            if data.get("provider_timeouts")
            else ""
        ),
        f"- Grading: {'model grade' if grading == 'model' else 'exact (2026-11-08 rules)'};"
        f" samples per cell: {samples}",
    ]
    profiles = data.get("pack_profiles") or {}
    if profiles:
        lines.append(
            "- Profiles: each pack runs on the harness profile its `pack.yaml` names"
            " (in brackets after the pack)"
        )
    if grading == "model":
        lines += [
            "",
            'Model grade (`spec/contracts/evaluator.md`, "Grading modes"): `tool_calls` passes',
            "when the expected calls appear in order, with extra calls to read tools allowed",
            "(any other extra call, or a call when none is expected, fails); `answer_match`",
            "compares normalized text (case, Unicode, whitespace, dates, times, amounts, numbers).",
        ]
    lines += [
        "",
        "## Models",
        "",
        "| Alias | Provider | Model | Parameters | Quantization | Temperature |",
        "|---|---|---|---|---|---|",
    ]
    for m in models:
        d = m.get("details") or {}
        lines.append(
            f"| `{m['alias']}` | {m['provider']} | `{_model_name(m)}` |"
            f" {d.get('parameter_size', '')} | {d.get('quantization_level', '')} |"
            f" {m.get('temperature') or 'provider default'} |"
        )
    lines += [
        "",
        "## Pass counts",
        "",
        "`passed/scored` per pack (a skipped case, outside its modes, is not scored)"
        + (", the mean over the samples with the min and max in brackets" if samples > 1 else "")
        + "; `n/a` means the model could not run here (the reason is under the model below).",
        "",
        "| Pack | " + " | ".join(f"`{a}`" for a in aliases) + " |",
        "|---|" + "---|" * len(aliases),
    ]
    for pack in data["packs"]:
        row = [_cell_text(lookup.get((pack, a))) for a in aliases]
        label = f"`{pack}` ({profiles[pack]})" if pack in profiles else f"`{pack}`"
        lines.append(f"| {label} | " + " | ".join(row) + " |")
    lines.append("| **All packs** | " + " | ".join(_total_text(stats[a]) for a in aliases) + " |")
    carried = data.get("carried")
    if carried:
        lines += [
            "",
            f"¹ Carried forward from the {carried['from']} run ({', '.join(carried['models'])},"
            " one sample, provider default temperature"
            + (
                ""
                if carried.get("profiles")
                else ", every pack on the 0.1 harness with every node on, before packs named a"
                " profile"
            )
            + ") and "
            + (
                "re-graded offline under model grade from the stored case results: tool calls"
                " and answer match from the stored calls and replies, schema from the answer"
                " before the output guardrails (trace) with the JSON extraction; path,"
                " citations, checks and PII as stored (their routing predates the pinned"
                " price-cap change, so a `lookup` rule check can still fail on a priced model)."
                if carried.get("regraded")
                else "kept with the 2026-11-08 grading (the stored case results were not found)."
            ),
        ]
    lines += [
        "",
        "## Per model",
        "",
        "Failures by scorer count failed cases per scorer (a case can fail several scorers);",
        "`error` counts cases whose turn hit an error (a provider error or a timeout)."
        + (" Counts are means per sample." if samples > 1 else ""),
        '"answer_match only" counts failed cases where every other scorer passed. The',
        "`tool_calls` split says why that scorer failed: a call when none was expected, no call",
        "at all, an expected write (non-read) tool not called, an expected read tool not called,",
        'or the expected calls made with an extra or wrong one. "Fails where most pass" lists',
        "the cases this model fails that at least half the other real models pass.",
    ]
    for m in models:
        a = m["alias"]
        s = stats[a]
        lines += ["", f"### `{a}`", ""]
        if not s["ran"]:
            lines.append(f"Not run: {'; '.join(r for r in s['reasons'] if r) or 'n/a'}.")
            continue
        if s["reasons"] and any(s["reasons"]):
            lines += [f"Some cells did not run: {'; '.join(r for r in s['reasons'] if r)}.", ""]
        lines += [
            "| " + " | ".join(SCORER_COLUMNS) + " |",
            "|" + "---|" * len(SCORER_COLUMNS),
            "| " + " | ".join(_num(s["by_scorer"][n]) for n in SCORER_COLUMNS) + " |",
            "",
        ]
        if s["failed"]:
            lines += [
                "| " + " | ".join(f"tool_calls: {n}" for n in TOOL_SPLIT) + " |",
                "|" + "---|" * len(TOOL_SPLIT),
                "| " + " | ".join(_num(s["tool_split"][n]) for n in TOOL_SPLIT) + " |",
                "",
                f"- {_num(s['failed'])} failed cases; answer_match only:"
                f" {_num(s['answer_match_only'])}",
            ]
            if s["outliers"]:
                shown = ", ".join(
                    f"`{o['case']}` ({o['others_passing']} of {o['others']})"
                    for o in s["outliers"][:5]
                )
                more = f" and {len(s['outliers']) - 5} more" if len(s["outliers"]) > 5 else ""
                lines.append(f"- Fails where most pass: {shown}{more}")
            else:
                lines.append("- Fails where most pass: none")
        else:
            lines.append("- No failed case")
        lines.append(
            f"- Tokens: {s['input_tokens']:,} in, {s['output_tokens']:,} out;"
            f" cost ${s['usd']:.4f}; wall time {s['seconds']:.0f} s"
        )
    lines += [
        "",
        "## Regenerate",
        "",
        "```sh",
        data.get("command") or "uv run harnesswalk scorecard",
        "```",
        "",
        "The run detail of every cell is in the scorecard JSON"
        + (f" (`{data['json_path']}`)" if data.get("json_path") else "")
        + "; `harnesswalk eval <pack> --suite model --model <alias>` replays one cell.",
        "The command raises the hosted providers' call timeout"
        " (`models.providers.<name>.timeout_s`) to at least `--case-timeout` unless a `--set`"
        " names it, and sends Anthropic no `temperature` (the API rejects it for these models).",
        "",
    ]
    return "\n".join(lines)


def scorecard_data(
    *,
    cells: list[dict[str, Any]],
    models: list[dict[str, Any]],
    packs: list[str],
    tenant: str,
    harness_version: str,
    spec_version: str,
    ollama: dict[str, Any] | None,
    **extra: Any,
) -> dict[str, Any]:
    """The scorecard JSON (also the input of `render_markdown`)."""
    return {
        "generated_at": iso(time.time())[:16].replace("T", " "),
        "harness_version": harness_version,
        "spec_version": spec_version,
        "tenant": tenant,
        "ollama": (
            {k: ollama.get(k) for k in ("host", "version", "reachable", "error")}
            if ollama
            else None
        ),
        "packs": packs,
        "models": models,
        "cells": cells,
        **extra,
    }
