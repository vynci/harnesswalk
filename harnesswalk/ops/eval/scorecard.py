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


def cell_of(
    pack: str, alias: str, summary: dict[str, Any], cases: list[dict[str, Any]]
) -> dict[str, Any]:
    """A finished eval run reduced to a scorecard cell: totals, cost, time, and per case the
    status, the failing scorers and the turn's error codes."""
    reduced = []
    for c in cases:
        failing = [name for name, s in (c.get("scores") or {}).items() if not s.get("pass")]
        errors = [e.get("code") for e in (c.get("actual") or {}).get("errors") or []]
        if c.get("error"):
            errors.append(c["error"].get("code"))
        reduced.append({
            "case_id": c["case_id"], "status": c["status"], "failed": failing,
            "errors": [e for e in errors if e],
        })  # fmt: skip
    seconds = None
    if summary.get("started_at") and summary.get("ended_at"):
        seconds = round(_epoch(summary["ended_at"]) - _epoch(summary["started_at"]), 1)
    cell: dict[str, Any] = {
        "pack": pack,
        "model": alias,
        "status": "ok" if summary.get("status") == "done" else "error",
        "reason": (summary.get("error") or {}).get("message"),
        "run_id": summary.get("run_id"),
        "suite": summary.get("suite"),
        "totals": summary.get("totals"),
        "cost": summary.get("cost"),
        "seconds": seconds,
        "cases": reduced,
    }
    return cell


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
            t = cell.get("totals") or {}
            took = time.monotonic() - started
            if cell["status"] == "ok":
                log(f"{alias:<14} {pack:<12} {_ratio(t)} in {took:.0f}s")
            else:
                log(f"{alias:<14} {pack:<12} {cell['status']}: {cell.get('reason')}")
        if finish is not None and not reasons.get(alias):
            try:
                await finish(alias)
            except Exception:  # an unload that fails never stops the matrix
                log(f"{alias:<14} could not be unloaded")
    return cells


def _ratio(totals: dict[str, Any]) -> str:
    scored = int(totals.get("cases", 0)) - int(totals.get("skipped", 0))
    return f"{int(totals.get('passed', 0))}/{scored}"


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    """Per model: pass counts, failures by scorer, the most common failing cases, tokens and
    cost. `data` is the scorecard JSON (`cells`, `models`, `packs`)."""
    cells = data["cells"]
    aliases = [m["alias"] for m in data["models"]]
    # How many models fail each (pack, case): "most common" ranks a model's failing cases by it.
    failing_models: Counter[tuple[str, str]] = Counter()
    for cell in cells:
        for c in cell.get("cases") or []:
            if c["status"] in ("failed", "error"):
                failing_models[(cell["pack"], c["case_id"])] += 1
    out: dict[str, Any] = {}
    for alias in aliases:
        mine = [c for c in cells if c["model"] == alias]
        ran = [c for c in mine if c["status"] == "ok"]
        by_scorer: Counter[str] = Counter()
        wording_only = 0  # failed answer_match and nothing else: the facts may be right
        failed_cases: list[tuple[str, str, int]] = []
        tokens_in = tokens_out = 0
        usd = 0.0
        seconds = 0.0
        passed = scored = skipped = 0
        for cell in ran:
            t = cell.get("totals") or {}
            passed += int(t.get("passed", 0))
            skipped += int(t.get("skipped", 0))
            scored += int(t.get("cases", 0)) - int(t.get("skipped", 0))
            cost = cell.get("cost") or {}
            tokens_in += int(cost.get("input_tokens", 0))
            tokens_out += int(cost.get("output_tokens", 0))
            usd += float(cost.get("usd", 0.0))
            seconds += float(cell.get("seconds") or 0.0)
            for position, c in enumerate(cell.get("cases") or []):
                if c["status"] not in ("failed", "error"):
                    continue
                for name in c.get("failed") or []:
                    by_scorer[name] += 1
                if c["status"] == "error" or c.get("errors"):
                    by_scorer["error"] += 1
                elif (c.get("failed") or []) == ["answer_match"]:
                    wording_only += 1
                failed_cases.append((cell["pack"], c["case_id"], position))
        pack_order = {p: i for i, p in enumerate(data["packs"])}
        failed_cases.sort(
            key=lambda f: (-failing_models[(f[0], f[1])], pack_order.get(f[0], 99), f[2])
        )
        out[alias] = {
            "ran": len(ran),
            "cells": len(mine),
            "passed": passed,
            "scored": scored,
            "skipped": skipped,
            "failed": len(failed_cases),
            "by_scorer": {name: by_scorer.get(name, 0) for name in SCORER_COLUMNS},
            "answer_match_only": wording_only,
            "common_failures": [
                {"case": f"{p}/{cid}", "models_failing": failing_models[(p, cid)]}
                for p, cid, _ in failed_cases[:3]
            ],
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "usd": round(usd, 6),
            "seconds": round(seconds, 1),
            "reasons": sorted({c.get("reason") or "" for c in mine if c["status"] != "ok"}),
        }
    return out


# -- Markdown ----------------------------------------------------------------------------------


def _cell_text(cell: dict[str, Any] | None) -> str:
    if cell is None:
        return ""
    if cell["status"] == "n/a":
        return "n/a"
    if cell["status"] != "ok":
        return "error"
    return _ratio(cell.get("totals") or {})


def _model_name(m: dict[str, Any]) -> str:
    return m.get("name") or m["alias"]


def render_markdown(data: dict[str, Any]) -> str:
    """docs/scorecard.md from the scorecard JSON."""
    stats = summarize(data)
    models = data["models"]
    aliases = [m["alias"] for m in models]
    lookup = {(c["pack"], c["model"]): c for c in data["cells"]}
    ollama = data.get("ollama")
    lines = [
        "# Model ladder scorecard",
        "",
        "Which model passes which pack's golden set when the model really answers. Each cell is",
        "one `model`-suite eval run: every golden case of the pack, answered by the model alias",
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
        f" case timeout: {data.get('case_timeout_s', 60)} s",
        "",
        "## Models",
        "",
        "| Alias | Provider | Model | Parameters | Quantization |",
        "|---|---|---|---|---|",
    ]
    for m in models:
        d = m.get("details") or {}
        lines.append(
            f"| `{m['alias']}` | {m['provider']} | `{_model_name(m)}` |"
            f" {d.get('parameter_size', '')} | {d.get('quantization_level', '')} |"
        )
    lines += [
        "",
        "## Pass counts",
        "",
        "`passed/scored` per pack (a skipped case, outside its modes, is not scored); `n/a` means",
        "the model could not run here (the reason is under the model below).",
        "",
        "| Pack | " + " | ".join(f"`{a}`" for a in aliases) + " |",
        "|---|" + "---|" * len(aliases),
    ]
    for pack in data["packs"]:
        row = [_cell_text(lookup.get((pack, a))) for a in aliases]
        lines.append(f"| `{pack}` | " + " | ".join(row) + " |")
    totals = []
    for a in aliases:
        s = stats[a]
        if not s["ran"]:
            totals.append("n/a")
            continue
        pct = 100.0 * s["passed"] / s["scored"] if s["scored"] else 0.0
        totals.append(f"**{s['passed']}/{s['scored']}** ({pct:.0f}%)")
    lines.append("| **All packs** | " + " | ".join(totals) + " |")
    ran_models = sum(1 for a in aliases if stats[a]["ran"])
    lines += [
        "",
        "## Per model",
        "",
        "Failures by scorer count failed cases per scorer (a case can fail several scorers);",
        "`error` counts cases whose turn hit an error (a provider error or a timeout). The most",
        "common failing cases are the ones the most models in this matrix also fail.",
        "`answer_match` checks exact substrings written for the fixture replies, so a real model",
        'that words a right answer differently fails it; "answer_match only" counts the failed',
        "cases where every other scorer passed (read those replies before calling them wrong).",
        "`tool_calls` is equality with the fixture's tool sequence, so a model that also reads a",
        "skill or looks a record up before acting fails it even when the extra call is sensible.",
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
            "| " + " | ".join(str(s["by_scorer"][n]) for n in SCORER_COLUMNS) + " |",
            "",
        ]
        if s["common_failures"]:
            common = ", ".join(
                f"`{f['case']}` ({f['models_failing']} of {ran_models} models)"
                for f in s["common_failures"]
            )
            lines.append(f"- {s['failed']} failed cases; most common: {common}")
            lines.append(f"- answer_match only: {s['answer_match_only']} of the failed cases")
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
