"""`harnesswalk init` end to end: a generated repo proves itself with the conformance suite.

Gate 1 of the P4 brief: a minimal repo (helpdesk_lite) passes every conformance case its
profile satisfies, on the fake provider, through the vendored runner in the output and through
this repository's own runner (`--command`), with zero Harnesswalk imports, and passes its
pack's golden set.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from harnesswalk.gen import build
from harnesswalk.gen.manifest import GenManifest
from tests.conformance import runner as official

IMPORT = re.compile(r"^\s*(from|import)\s+harnesswalk\b", re.M)
PUBLIC = {
    "injection_blocked_public",
    "memory_denied_public",
    "public_budget_fall_through",
    "public_model_allowlist",
}


def generate(tmp: Path, profile: str, pack: str, **kw: object) -> Path:
    out = tmp / f"{profile}-{pack}"
    build.init(GenManifest(profile=profile, pack=pack, **kw), out, use_git=False)  # type: ignore[arg-type]
    return out


def vendored(out: Path, *args: str) -> tuple[int, list[dict]]:
    report = out / "conformance.json"
    done = subprocess.run(
        [sys.executable, "-m", "tests.conformance.runner", "--json", str(report), *args],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return done.returncode, json.loads(report.read_text())


def run_official(out: Path, profile: str) -> dict[str, official.CaseResult]:
    command = (
        f"env PYTHONPATH={out} {sys.executable} -m harness serve"
        " --port {port} --data {data_dir}"
        " --fixture {fixture} --mode {mode} --profile {profile} {set_args}"
    )
    target = official.Target(command=command, profile=profile)
    return {p.stem: official.run_case(p, target) for p in official.case_paths()}


@pytest.fixture(scope="module")
def minimal(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate(tmp_path_factory.mktemp("gen"), "minimal", "helpdesk_lite")


def test_minimal_layout_lock_and_notes(minimal: Path) -> None:
    for rel in (
        "harness/core/gateway.py",
        "prompts/system_prompt.md",
        "tools/tools.yaml",
        "policy/policy.yaml",
        "packs/helpdesk_lite/golden.jsonl",
        "harness.yaml",
        "harness.lock",
        "GENERATION_NOTES.md",
        "compose.yaml",
        "artifacts/Modelfile",
        ".harnesswalk/pristine/harness/core/gateway.py",
        "tests/conformance/runner.py",
    ):
        assert (minimal / rel).is_file(), rel
    assert not (minimal / "harness/core/policy.py").exists()  # off nodes are seams, not files
    first = (minimal / "README.md").read_text().split("\n\n")[1]
    assert "no runtime dependency on Harnesswalk" in first.replace("\n", " ")
    notes = (minimal / "GENERATION_NOTES.md").read_text()
    assert "`tune`" in notes and "/v1/turns/{id}/events" in notes


def test_minimal_passes_conformance(minimal: Path) -> None:
    code, results = vendored(minimal)
    ran = {r["case"]: r["status"] for r in results if r["status"] != "SKIP"}
    assert code == 0 and ran == {"profile_minimal": "PASS", "profile_minimal_tool": "PASS"}
    skipped = {r["case"]: r["detail"] for r in results if r["status"] == "SKIP"}
    assert all("public mode" in skipped[c] for c in PUBLIC)


def test_minimal_passes_the_official_runner(minimal: Path) -> None:
    results = run_official(minimal, "minimal")
    failed = {c: r.problems[0] for c, r in results.items() if r.problems}
    assert set(failed) <= PUBLIC, failed  # public mode is never generated
    assert all("not generated" in p for p in failed.values())
    assert results["profile_minimal"].passed and results["profile_minimal_tool"].passed


@pytest.mark.parametrize("which", ["minimal"])
def test_golden_set_passes_and_no_harnesswalk_import(
    which: str, request: pytest.FixtureRequest
) -> None:
    out = request.getfixturevalue(which)
    done = subprocess.run(
        [sys.executable, "-m", "evals.run_golden"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert done.returncode == 0, done.stdout[-2000:]
    assert re.search(r"(\d+)/\1 golden cases passed", done.stdout)
    hits = [p for p in out.rglob("*.py") if IMPORT.search(p.read_text())]
    assert hits == []
    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "."],
        cwd=out,
        capture_output=True,
        text=True,
    )
    assert lint.returncode == 0, lint.stdout[-2000:]


def test_line_budget(minimal: Path) -> None:
    assert build.line_counts(minimal)["python"] < 3000


def test_files_storage_and_cli_transport(tmp_path: Path) -> None:
    out = generate(tmp_path, "minimal", "helpdesk_lite", storage="files")
    code, _ = vendored(out)
    assert code == 0 and "JSON Lines" in (out / "harness/core/db.py").read_text()
    cli = generate(tmp_path / "cli", "minimal", "helpdesk_lite", transport="cli")
    assert (
        not (cli / "harness/core/http_api.py").exists() and not (cli / "tests/conformance").exists()
    )


def test_profiles_without_templates_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no template implements"):
        generate(tmp_path, "governed", "device_fleet")
    with pytest.raises(ValueError, match="--tune"):
        generate(tmp_path, "minimal", "helpdesk_lite", tune=True)
