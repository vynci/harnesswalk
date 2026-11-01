"""Generate one synthetic dataset pack with a hosted model, then finish it locally.

    uv run python tools/gen_pack.py <pack> [--stage core|docs|golden|finalize|all] [--force]

The hosted stages (`core`, `docs`, `golden`) send `tools/packs/_common.prompt.md` and
`tools/packs/<pack>.prompt.md` to `claude-sonnet-5-5` over the Messages API (plain httpx,
streamed) and write the files the model returns into `datasets/<pack>/`. The key is read from
the `ANTHROPIC_API_KEY` environment variable only; nothing here prints or stores it. Each call
appends its token usage to `tools/packs/<pack>.usage.jsonl`, which the provenance card sums.

`finalize` needs no network: it adds the pack's fixed fields and guardrails, removes
em-dashes, renders the PDF documents (`fpdf2`, a dev dependency), turns `[[cite:<doc>]]`
markers in the fake fixtures into the passage numbers the harness retrieves for each golden
case, and writes `provenance.md`. A stage whose outputs exist is skipped unless `--force`.

Generated packs are reviewed and edited by hand after this script; the provenance card says
so. The content is synthetic and dedicated to the public domain (CC0 1.0).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "tools" / "packs"
DATASETS = ROOT / "datasets"
MODEL = "claude-sonnet-5-5"
PRICE_IN, PRICE_OUT = 2.0, 10.0  # dollars per million tokens (config/models.yaml hosted-sonnet)
API = "https://api.anthropic.com/v1/messages"
STAGES = ("core", "docs", "golden", "finalize")
DOCS_PER_CALL = 4
FILE_BLOCK = re.compile(
    r"^=== FILE: (?P<path>[^\n]+?) ===\n(?P<body>.*?)^=== END FILE ===$", re.M | re.S
)
SAFE_PATH = re.compile(r"^(?:[a-z0-9_]+/)*[a-z0-9_]+\.(?:md|yaml|jsonl|json)$")
CITE = re.compile(r"\[\[cite:([a-z0-9_]+)\]\]")

# The pack's input-guardrail overrides (datasets/<pack>/guardrails.yaml). Written by this
# script, not by the model, so the patterns are exactly what the validator scans for.
DEFAULT_IDS = [
    {"name": "us_ssn", "regex": r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])", "luhn": False},
    {"name": "ca_sin", "regex": r"(?<![\d-])\d{3}[ -]\d{3}[ -]\d{3}(?![\d-])", "luhn": True},
]
GUARDRAILS: dict[str, dict[str, Any]] = {
    "healthcare": {
        "comment": "Brindlemoor Health Numbers (BH-1234-5678-K) are national ids for this pack,"
        ' and names after a cue ("my name is ...") are redacted too.',
        "pii": {
            "national_id": {
                "patterns": [
                    *DEFAULT_IDS,
                    {
                        "name": "brindlemoor_health_number",
                        "regex": r"(?<![A-Za-z0-9-])BH-\d{4}-\d{4}-[A-Z](?![A-Za-z0-9-])",
                        "luhn": False,
                    },
                ]
            },
            "names": {"enabled": True},
        },
    },
    "finance": {
        "comment": "Investor account numbers (LP-1234-5678-90) are national ids for this pack.",
        "pii": {
            "national_id": {
                "patterns": [
                    *DEFAULT_IDS,
                    {
                        "name": "investor_account_number",
                        "regex": r"(?<![A-Za-z0-9-])LP-\d{4}-\d{4}-\d{2}(?![A-Za-z0-9-])",
                        "luhn": False,
                    },
                ]
            }
        },
    },
}
FEATURES = {
    "healthcare": "pii_redaction_and_refusal",
    "ecommerce": "approval",
    "logistics": "multi_step_tools",
    "sports": "router",
    "finance": "citations_and_memory",
}


# -- the hosted model -------------------------------------------------------------------------


def call_model(system: str, user: str, max_tokens: int) -> tuple[str, dict[str, int]]:
    """One streamed Messages API call. Returns the text and the usage."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("set ANTHROPIC_API_KEY for the hosted stages (never in a file)")
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "thinking": {"type": "between_tools"},  # Sonnet 5.5's thinking-off mode
        "stream": True,
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    text: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    stop = None
    with httpx.stream("POST", API, json=body, headers=headers, timeout=900) as resp:
        if resp.status_code != 200:
            resp.read()
            raise SystemExit(f"API error {resp.status_code}: {resp.text[:500]}")
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            kind = event.get("type")
            if kind == "message_start":
                usage["input_tokens"] = event["message"]["usage"].get("input_tokens", 0)
            elif kind == "content_block_delta" and event["delta"].get("type") == "text_delta":
                text.append(event["delta"]["text"])
            elif kind == "message_delta":
                usage["output_tokens"] = event.get("usage", {}).get("output_tokens", 0)
                stop = event.get("delta", {}).get("stop_reason")
            elif kind == "error":
                raise SystemExit(f"stream error: {event.get('error')}")
    if stop not in ("end_turn", "stop_sequence"):
        print(f"  warning: stop_reason {stop}", file=sys.stderr)
    return "".join(text), usage


def record_usage(pack: str, stage: str, usage: dict[str, int]) -> None:
    usd = (usage["input_tokens"] * PRICE_IN + usage["output_tokens"] * PRICE_OUT) / 1e6
    line = {
        "stage": stage,
        "model": MODEL,
        "date": dt.date.today().isoformat(),
        **usage,
        "usd": round(usd, 4),
    }
    with (PROMPTS / f"{pack}.usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    print(f"  {stage}: {usage['input_tokens']} in / {usage['output_tokens']} out, ${usd:.4f}")


def parse_files(text: str) -> dict[str, str]:
    files = {}
    for m in FILE_BLOCK.finditer(text):
        path = m.group("path").strip()
        if not SAFE_PATH.match(path):
            print(f"  skipped unsafe path {path!r}", file=sys.stderr)
            continue
        files[path] = m.group("body").rstrip() + "\n"
    return files


def write_files(pack_dir: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        target = pack_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        print(f"  wrote {target.relative_to(ROOT)}")


# -- prompts ------------------------------------------------------------------------------------


def common() -> str:
    return (PROMPTS / "_common.prompt.md").read_text(encoding="utf-8")


def brief(pack: str) -> str:
    return (PROMPTS / f"{pack}.prompt.md").read_text(encoding="utf-8")


def context_files(pack_dir: Path, names: list[str]) -> str:
    out = []
    for rel in names:
        for path in sorted(pack_dir.glob(rel)):
            rel_path = path.relative_to(pack_dir)
            out.append(f"=== FILE: {rel_path} ===\n{path.read_text()}=== END FILE ===")
    return "\n\n".join(out)


CORE_FILES = [
    "pack.yaml", "system_prompt.md", "skills/*.md", "tools.yaml", "facts.jsonl", "episodes.jsonl"
]  # fmt: skip


def stage_core(pack: str, pack_dir: Path) -> None:
    user = f"{brief(pack)}\n\nRun the stage: core. Write every core file now."
    text, usage = call_model(common(), user, 32000)
    record_usage(pack, "core", usage)
    files = parse_files(text)
    plan = files.pop("docs_plan.json", None)
    if plan is None:
        raise SystemExit("the core stage returned no docs_plan.json")
    (PROMPTS / f"{pack}.plan.json").write_text(plan, encoding="utf-8")
    files.pop("guardrails.yaml", None)  # written by finalize
    write_files(pack_dir, files)


def stage_docs(pack: str, pack_dir: Path, force: bool) -> None:
    plan = json.loads((PROMPTS / f"{pack}.plan.json").read_text(encoding="utf-8"))
    todo = [d for d in plan if force or not doc_exists(pack_dir, d["id"])]
    ctx = context_files(pack_dir, CORE_FILES)
    for i in range(0, len(todo), DOCS_PER_CALL):
        batch = todo[i : i + DOCS_PER_CALL]
        listing = "\n".join(f"- `docs/{d['id']}.md`: {d['title']}. {d['brief']}" for d in batch)
        others = ", ".join(f"{d['id']} ({d['title']})" for d in plan)
        user = (
            f"{brief(pack)}\n\nThe core files:\n\n{ctx}\n\nAll documents of the pack: {others}."
            f"\n\nRun the stage: docs. Write these documents now:\n{listing}"
        )
        text, usage = call_model(common(), user, 16000)
        record_usage(pack, f"docs[{i // DOCS_PER_CALL}]", usage)
        write_files(pack_dir, parse_files(text))


def doc_exists(pack_dir: Path, doc_id: str) -> bool:
    return (pack_dir / "docs" / f"{doc_id}.md").exists() or (
        pack_dir / "sources" / f"{doc_id}.md"
    ).exists()


def stage_golden(pack: str, pack_dir: Path) -> None:
    ctx = context_files(pack_dir, [*CORE_FILES, "docs/*.md", "sources/*.md"])
    user = (
        f"{brief(pack)}\n\nThe pack so far:\n\n{ctx}\n\nRun the stage: golden. Write"
        " golden.jsonl and every fixture now. Doc ids are the file stems under docs/."
    )
    text, usage = call_model(common(), user, 32000)
    record_usage(pack, "golden", usage)
    write_files(pack_dir, parse_files(text))


# -- finalize (local) ---------------------------------------------------------------------------


def no_em_dashes(pack_dir: Path) -> None:
    for path in pack_dir.rglob("*"):
        if path.suffix in (".md", ".yaml", ".jsonl", ".json") and path.is_file():
            text = path.read_text(encoding="utf-8")
            fixed = re.sub(r"\s*\u2014\s*", ", ", text)
            if fixed != text:
                path.write_text(fixed, encoding="utf-8")


def finish_pack_yaml(pack: str, pack_dir: Path) -> None:
    path = pack_dir / "pack.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ordered = {
        "id": pack,
        "title": data.get("title"),
        "domain": data.get("domain", pack),
        "summary": data.get("summary"),
        "tier": "domain",
        "feature": {"id": FEATURES[pack], **(data.get("feature") or {})},
        "licence": "CC0-1.0",
        "provenance": "provenance.md",
        "synthetic": True,
        "suggested_prompts": data.get("suggested_prompts") or [],
    }
    path.write_text(yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100))


def write_guardrails(pack: str, pack_dir: Path) -> None:
    spec = GUARDRAILS.get(pack)
    if spec is None:
        return
    head = (
        "# Pack overrides for the input guardrails (spec/contracts/guardrail.md). The patterns\n"
        "# replace the defaults for this pack, so the US SSN and Canadian SIN ones are kept.\n"
        f"# {spec['comment']}\n"
    )
    body = yaml.safe_dump({"pii": spec["pii"]}, sort_keys=False, width=200)
    (pack_dir / "guardrails.yaml").write_text(head + body, encoding="utf-8")


def render_pdfs(pack: str, pack_dir: Path) -> list[str]:
    """Each doc marked `pdf` moves to sources/<id>.md and is rendered as docs/<id>.pdf."""
    from fpdf import FPDF

    plan = json.loads((PROMPTS / f"{pack}.plan.json").read_text(encoding="utf-8"))
    done = []
    for doc in (d for d in plan if d.get("pdf")):
        md = pack_dir / "docs" / f"{doc['id']}.md"
        src = pack_dir / "sources" / f"{doc['id']}.md"
        if md.exists():
            src.parent.mkdir(exist_ok=True)
            shutil.move(md, src)
        if not src.exists():
            continue
        pdf = FPDF()
        pdf.set_title(doc["title"])
        pdf.set_author("harnesswalk synthetic pack (CC0)")
        pdf.set_creation_date(dt.datetime(2026, 11, 1, tzinfo=dt.UTC))
        pdf.set_auto_page_break(True, margin=18)
        pdf.add_page()
        for raw in src.read_text(encoding="utf-8").splitlines():
            line = ascii_text(raw)
            if line.startswith("# "):
                pdf.set_font("Helvetica", "B", 16)
                pdf.multi_cell(0, 9, line[2:], new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
            elif line.startswith("## ") or line.startswith("### "):
                pdf.ln(2)
                pdf.set_font("Helvetica", "B", 12)
                pdf.multi_cell(0, 7, line.lstrip("#").strip(), new_x="LMARGIN", new_y="NEXT")
            elif line.strip():
                pdf.set_font("Helvetica", "", 10)
                text = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
                text = re.sub(r"^\s*[-*] ", "- ", text)
                pdf.multi_cell(0, 5.5, text, new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.ln(2)
        pdf.output(str(pack_dir / "docs" / f"{doc['id']}.pdf"))
        done.append(doc["id"])
    return done


def ascii_text(text: str) -> str:
    """The core PDF fonts are Latin-1: fold the few typographic characters they lack."""
    table = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
             "\u2014": ", ", "\u2026": "...", "\u2022": "-", "\u00a0": " ", "\u2264": "<=",
             "\u2265": ">=", "\u2192": "->", "\u00d7": "x"}  # fmt: skip
    text = "".join(table.get(ch, ch) for ch in text)
    return text.encode("latin-1", "replace").decode("latin-1")


def resolve_citations(pack: str) -> list[str]:
    """Replace `[[cite:<doc>]]` in each case's fixture with the number that doc's best passage
    gets in that case's context (fake provider, hash embeddings: the same on every machine)."""
    from harnesswalk.ops.eval.golden import load_golden

    pack_dir = DATASETS / pack
    cases = load_golden(pack_dir / "golden.jsonl")
    pending = []
    for case in cases:
        path = pack_dir / "fixtures" / "fake" / f"{case.fixture}.jsonl"
        if case.fixture and path.exists() and CITE.search(path.read_text(encoding="utf-8")):
            pending.append((case, path))
    if not pending:
        return []
    problems = []
    numbering = asyncio.run(_numbering(pack, [c for c, _ in pending]))
    for case, path in pending:
        numbers = numbering.get(case.id, {})

        def put(m: re.Match[str], numbers: dict[str, int] = numbers, case: Any = case) -> str:
            n = numbers.get(m.group(1))
            if n is None:
                problems.append(f"{case.id}: {m.group(1)} is not retrieved for this prompt")
                return m.group(0)
            return f"[{n}]"

        path.write_text(CITE.sub(put, path.read_text(encoding="utf-8")), encoding="utf-8")
    return problems


async def _numbering(pack: str, cases: list[Any]) -> dict[str, dict[str, int]]:
    from harnesswalk.config.loader import load_settings
    from harnesswalk.control.gateway import TurnRequest
    from harnesswalk.runtime import Harness
    from harnesswalk.types import Identity

    out: dict[str, dict[str, int]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        settings = load_settings(
            ROOT,
            provider="fake",
            fixture="plain_answer",
            overrides={"dataset": pack, "sandbox.level": "inprocess"},
            data_dir=tmp,
            env={},
        )
        h = Harness(settings)
        try:
            for case in cases:
                req = TurnRequest(
                    message=case.prompt,
                    identity=Identity("gen", None, ["user"], anonymous=False),
                    session_id=f"sess_gen_{case.id}"[:120],
                    channel="cli",
                    provider="fake",
                    fixture=case.fixture,
                )
                state = await h.gateway.start_turn(req)

                def deny(event: dict[str, Any], turn: str = state.ctx.turn_id) -> None:
                    if event["type"] == "approval_requested" and event.get("turn_id") == turn:
                        approval_id = event["summary"]["approval_id"]
                        h.approvals.decide(approval_id, "gen", "gen", "deny", "")

                unsubscribe = h.bus.subscribe(deny)
                try:
                    await h.gateway.wait(state.ctx.turn_id, timeout=30)
                finally:
                    unsubscribe()
                events = h.tracer.get_turn("gen", state.ctx.turn_id)
                numbers: dict[str, int] = {}
                for e in events:
                    if e["type"] == "node_exit" and e.get("node_id") == "context_builder":
                        ref = e.get("detail_ref")
                        detail = h.tracer.get_detail("gen", ref) if ref else {}
                        for c in (detail or {}).get("citations") or []:
                            numbers.setdefault(c["doc"], c["n"])
                out[case.id] = numbers
        finally:
            await h.aclose()
    return out


def prompt_hashes(pack: str) -> dict[str, str]:
    out = {}
    for name in ("_common.prompt.md", f"{pack}.prompt.md"):
        out[f"tools/packs/{name}"] = hashlib.sha256((PROMPTS / name).read_bytes()).hexdigest()[:16]
    return out


def usage_rows(pack: str) -> list[dict[str, Any]]:
    path = PROMPTS / f"{pack}.usage.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_provenance(pack: str, pack_dir: Path, pdfs: list[str]) -> None:
    rows = usage_rows(pack)
    tin = sum(r["input_tokens"] for r in rows)
    tout = sum(r["output_tokens"] for r in rows)
    usd = sum(r["usd"] for r in rows)
    dates = sorted({r["date"] for r in rows}) or [dt.date.today().isoformat()]
    hashes = "\n".join(f"| `{k}` | `{v}` |" for k, v in prompt_hashes(pack).items())
    calls = "\n".join(
        f"| {r['stage']} | {r['input_tokens']} | {r['output_tokens']} | ${r['usd']:.4f} |"
        for r in rows
    )
    title = (yaml.safe_load((pack_dir / "pack.yaml").read_text()) or {}).get("title", pack)
    notes = PROMPTS / f"{pack}.review.md"
    review = notes.read_text(encoding="utf-8").strip() if notes.exists() else "- none"
    rendered = ", ".join(f"`docs/{d}.pdf` (source `sources/{d}.md`)" for d in pdfs) or "none"
    text = f"""# Provenance: {pack}

**{title}** is a synthetic dataset pack for harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (stages core, docs, golden, then finalize) |
| Model | `{MODEL}` (Anthropic Messages API), thinking off |
| Generated on | {", ".join(dates)} |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/{pack}.prompt.md` |
| Document plan | `tools/packs/{pack}.plan.json` |
| PDF renderings | {rendered} |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
{hashes}

Token usage and cost (prices from `config/models.yaml`, ${PRICE_IN:.2f} in and
${PRICE_OUT:.2f} out per million tokens):

| Call | Input tokens | Output tokens | Cost |
|---|---|---|---|
{calls}
| **Total** | **{tin}** | **{tout}** | **${usd:.4f}** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate {pack}` and
`harnesswalk eval {pack} --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

{review}

A maintainer's human review of the content (safety, licence card, no PII) is part of the P5
integration pass.
"""
    (pack_dir / "provenance.md").write_text(text, encoding="utf-8")


def stage_finalize(pack: str, pack_dir: Path) -> None:
    finish_pack_yaml(pack, pack_dir)
    write_guardrails(pack, pack_dir)
    no_em_dashes(pack_dir)
    pdfs = render_pdfs(pack, pack_dir)
    problems = resolve_citations(pack)
    write_provenance(pack, pack_dir, pdfs)
    print(f"  finalized {pack}: pdfs {pdfs}")
    for p in problems:
        print(f"  citation: {p}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pack", choices=sorted(FEATURES))
    parser.add_argument("--stage", choices=[*STAGES, "all"], default="all")
    parser.add_argument("--force", action="store_true", help="run a stage whose outputs exist")
    args = parser.parse_args(argv)
    pack_dir = DATASETS / args.pack
    pack_dir.mkdir(parents=True, exist_ok=True)
    stages = STAGES if args.stage == "all" else (args.stage,)
    for stage in stages:
        print(f"{args.pack}: {stage}")
        if stage == "core" and (args.force or not (pack_dir / "tools.yaml").exists()):
            stage_core(args.pack, pack_dir)
        elif stage == "docs":
            stage_docs(args.pack, pack_dir, args.force)
        elif stage == "golden" and (args.force or not (pack_dir / "golden.jsonl").exists()):
            stage_golden(args.pack, pack_dir)
        elif stage == "finalize":
            stage_finalize(args.pack, pack_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
