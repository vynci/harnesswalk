"""GENERATION_NOTES.md: what the generated repo is, what it serves and what was not mapped.

Lossiness is shown, never hidden (the AgentCore EXPORT_NOTES idea in 03-AGENT-BUILDER): every
key of the profile, the pack or the manifest that the templates cannot carry is listed with
what to do about it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harnesswalk.gen.manifest import GenManifest

ROUTES = [
    ("GET", "/healthz", "readiness, provider and fixture (the runner checks it)"),
    ("GET", "/v1/capabilities", "what this build can do"),
    ("GET", "/v1/diagram", "the diagram, `?pack=` dims the profile's switched-off nodes"),
    ("GET", "/v1/profiles", "the ladder, and which rungs this repo can run"),
    ("GET", "/v1/packs, /v1/packs/{id}", "the pack and its card"),
    ("POST", "/v1/sessions; GET /v1/sessions/{id}, /v1/sessions/{id}/turns", "sessions"),
    ("POST", "/v1/turns; GET /v1/turns/{id}", "start a turn, its summary"),
    ("GET", "/v1/turns/{id}/events", "the SSE event stream (after, Last-Event-ID, follow)"),
    ("POST", "/v1/turns/{id}/resume, /v1/turns/{id}/cancel", "step mode and cancel"),
    ("GET", "/v1/nodes/{id}", "a node and its source file (the code peek)"),
    (
        "POST",
        "/v1/evals/runs; GET /v1/evals/runs, /v1/evals/runs/{id}, /v1/evals/golden",
        "golden runs",
    ),
    ("GET", "/v1/lessons", "an empty list: the lessons live in Harnesswalk"),
    ("GET", "/v1/events", "the ops event stream, kept alive; this repo emits no ops events"),
]
GOVERNED = [
    ("POST", "/v1/approvals/{id}", "decide an approval"),
    ("GET", "/v1/audit", "audit export and chain check"),
]
CHAIN = [
    ("GET, PUT", "/v1/artifacts, /v1/artifacts/{kind}/{name}", "the working set"),
    ("GET, POST", "/v1/releases, /v1/releases/{id}, /v1/releases/{id}/rollback", "releases"),
]
NOT_SERVED = (
    "`/v1/evals/runs/{id}/events` (the run progress stream), "
    "`/v1/evals/runs/{id}/cases/{case}/events`, `/v1/trace/*` (receipts, payload details), "
    "`/v1/turns/{id}/feedback`, `/v1/diagnose`, `/v1/memory/proposals`, `/v1/knowledge/docs`, "
    "`/v1/packs/{id}/files/*`, `/v1/nodes/{id}/source`, `/v1/lessons/{slug}`, `/readyz`, "
    "`/v1/ops/budget`; serving the web app itself"
)


def unmapped(m: GenManifest, profile: Any, pack: Any, generated: set[str], root: Path) -> list[str]:
    out = [
        "Modes `public` and `private` are never generated (`modes: [dev]`): no anonymous tenant "
        "cookie, rate limits, hosted budget, TTL sweeper or bring-your-own key. The conformance "
        "cases that run in public mode are reported as not applicable.",
        "Budgets: only `policy.budgets.default.tool_calls_per_turn` (and per tenant) is enforced. "
        "The hourly ledgers and the per-turn token and dollar budgets of `policy.budgets` are not "
        "generated; `loop.budget_tokens` and `loop.budget_usd` still cap a turn.",
        "Optional stream features not generated: `message_delta` (the reply arrives on "
        "`turn_end`), `edge` events (edges are implied by consecutive nodes), payload detail "
        "records (`has_payload`, `detail_ref`), OpenTelemetry export, ops events "
        "(`artifact_changed`, `artifact_released`, eval run streams).",
        "Ops plane above this rung: online eval, judged suites, diagnose, feedback and the "
        "release gate are not generated (the top rung, `operated`, has no template set yet).",
        "The memory writer's `review` gate has no HTTP route here (`/v1/memory/proposals`): a "
        "proposal that looks like an instruction waits in the `proposals` table.",
        "`409 turn_running` on artifact edits while a turn runs, `data/owner.lock` and "
        "`--takeover` are not generated: run one server per data directory.",
    ]
    if m.tune:
        out.append(
            "Tuning (`--tune`): `curate` and `tune` are generated in miniature "
            "(`harness/core/tune.py`, `python -m harness tune curate|run`): golden cases only "
            "(no teacher rewrites, no prompt arm), the `fake` trainer and an `external` one that "
            "writes the dataset and a Modelfile for you to train elsewhere. Compare (the model "
            "gate), promote, the canary rule and rollback from spec/contracts/tuning.md are not "
            "generated, and there is no `/v1/tune` HTTP API (the ops events are in the "
            "`ops_events` table), so the conformance cases that drive `/v1/tune` are skipped."
        )
    else:
        out.append(
            "`curate` and `tune` (the tuning loop) are not generated: run `harnesswalk init` "
            "with `--tune` to add them in miniature; the diagram serves them as not implemented, "
            "and the conformance cases that drive `/v1/tune` are skipped."
        )
    pdfs = (
        [p.name for p in (pack.path / "docs").glob("*.pdf")]
        if (pack.path / "docs").exists()
        else []
    )
    if pdfs:
        out.append(
            f"PDF documents ({', '.join(sorted(pdfs))}) were converted to text page by page at "
            "generation time (`packs/<pack>/sources/<doc>.pages.json`): the output has no PDF "
            "library, so a PDF you add later needs the same step (or add pypdf)."
        )
    cases = []
    gp = pack.path / "golden.jsonl"
    for line in gp.read_text().splitlines() if gp.exists() else []:
        if line.strip():
            cases.append(json.loads(line))
    for c in cases:
        checks = dict(c.get("checks") or {})
        for t in c.get("turns") or []:
            checks.update(t.get("checks") or {})
        odd = sorted(set(checks) - {"router_rules", "compacted"})
        if odd:
            out.append(
                f"Golden case `{c['id']}`: `checks.{', checks.'.join(odd)}` is not scored "
                "by the generated runner (it reads the context builder's stored detail)."
            )
    tools_text = (
        (pack.path / "tools.yaml").read_text() if (pack.path / "tools.yaml").exists() else ""
    )
    fixtures = (
        " ".join(p.read_text() for p in (pack.path / "fixtures").rglob("*") if p.is_file())
        if (pack.path / "fixtures").exists()
        else ""
    )
    if '"calculator"' in fixtures or "calculator" in tools_text:
        out.append("The built-in `calculator` tool is not generated; add it to `tools/` as code.")
    card_profile = pack.meta.get("profile")
    if card_profile and card_profile != profile.id:
        out.append(
            f"The pack's card names profile `{card_profile}`; this repo runs `{profile.id}`. "
            "Golden cases that need a node this profile switches off will fail."
        )
    return out


def notes(m: GenManifest, profile: Any, pack: Any, generated: set[str], root: Path) -> str:
    routes = (
        ROUTES
        + (GOVERNED if "approval" in generated else [])
        + (CHAIN if m.release == "chain" else [])
    )
    off = sorted(profile.disable)
    lines = [
        "# Generation notes",
        "",
        f"Generated by `harnesswalk init` from profile `{profile.id}` (rung {profile.rung}), pack "
        f"`{pack.id}` and the manifest in `harness.yaml`. Template set `{m.template_set}`; the "
        "versions are in `harness.lock`.",
        "",
        "## Nodes",
        "",
        f"Generated (code in `harness/core/`): {', '.join(sorted(generated))}.",
        "",
        f"Switched off by the profile, visited as skipped spans through the `off_node` seam: "
        f"{', '.join(off) or 'none'}.",
        "",
        "## The HTTP subset served",
        "",
        "Enough for the Harnesswalk web app and the conformance runner "
        "(`spec/contracts/http_api.md`). Point the web app at it with "
        "`HARNESSWALK_API_URL=http://127.0.0.1:8000 make web-dev` in a Harnesswalk checkout.",
        "",
        "| Method | Path | What for |",
        "|---|---|---|",
        *[f"| {a} | `{b}` | {c} |" for a, b, c in routes],
        "",
        f"Not served: {NOT_SERVED}.",
        "",
        "## Not mapped",
        "",
        *[f"- {item}" for item in unmapped(m, profile, pack, generated, root)],
        "",
    ]
    return "\n".join(lines)


# Why each simple node exists and when you could remove it (the README's table).
NODE_NOTES = {
    "gateway": ("assigns tenant, session, turn and trace ids, and closes every stream", "never"),
    "input_guardrails": (
        "PII never reaches the model or memory; oversized or hostile input is refused"
        " before it costs anything",
        "only for a tool nobody outside your team can reach",
    ),
    "policy": (
        "decides every tool call from rules in a file; prompt text cannot change it",
        "only when no tool can do harm and nobody will ask who allowed what",
    ),
    "approval": (
        "holds an irreversible action until a human says yes",
        "when no tool needs a human; it fails closed when off",
    ),
    "audit": (
        "an append-only hash chain of every consequential decision",
        "together with policy and approval, never alone",
    ),
    "context_builder": ("assembles what the model sees inside a budget, stable first", "never"),
    "memory": ("the read side of every store, behind one interface", "never"),
    "memory_writer": (
        "the only gated write path into durable memory",
        "when nothing should outlive a session",
    ),
    "loop": (
        "calls the model and the tools until an answer, with a ceiling on everything",
        "never",
    ),
    "model_layer": ("routes by rules, speaks to providers, reports cost", "never"),
    "tools": ("resolves, validates and sandboxes every call the model asks for", "never"),
    "output_guardrails": (
        "checks the answer's shape, citations and leaks before it leaves",
        "only when nothing downstream parses the reply and no documents are cited",
    ),
    "trace": ("keeps every event, so a turn can be replayed, evaluated and priced", "never"),
    "eval": ("online judging and diagnosis (not generated on this rung)", "n/a"),
    "release": ("the eval gate on releases (not generated on this rung)", "n/a"),
}


def node_table(simple: list[str], generated: set[str]) -> str:
    rows = ["| Node | Here | Why it exists | When to remove it |", "|---|---|---|---|"]
    for node in simple:
        why, remove = NODE_NOTES.get(node, ("", ""))
        rows.append(
            f"| `{node}` | {'yes' if node in generated else 'off (dimmed)'} | {why} | {remove} |"
        )
    return "\n".join(rows)
