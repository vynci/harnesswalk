"""Gate 5: `harnesswalk upgrade` from template set N to N+1 merges the managed change on a new
branch, keeps the user's edit to a managed file, and leaves owned files and the working tree
alone; the codemod hook runs once."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

from harnesswalk.gen import build, upgrade
from harnesswalk.gen.manifest import GenManifest


def git(repo: Path, *args: str) -> str:
    return build.git(repo, *args)


def test_upgrade_merges_managed_files_on_a_branch(tmp_path: Path) -> None:
    n = tmp_path / "templates-n"
    shutil.copytree(build.TEMPLATES, n)
    out = tmp_path / "repo"
    build.init(GenManifest(profile="minimal", pack="helpdesk_lite"), out, base=n)
    # The user edits an owned file and, in one place, a managed one.
    prompt = out / "prompts" / "system_prompt.md"
    prompt.write_text(prompt.read_text() + "\nAlways sign off as the Pebblebrook desk.\n")
    loop = out / "harness" / "core" / "loop.py"
    loop.write_text(
        loop.read_text().replace(
            "I stopped because the turn was interrupted.", "Stopped: you interrupted the turn."
        )
    )
    git(out, "commit", "-qam", "my edits")
    # Template set N+1: a tiny change to two managed files, one of them the file the user edited.
    n1 = tmp_path / "templates-n1"
    shutil.copytree(n, n1)
    meta = n1 / "python" / "manifest.yaml"
    meta.write_text(meta.read_text().replace("version: 0.1.0", "version: 0.1.1"))
    events = n1 / "python" / "files" / "harness" / "core" / "events.py"
    events.write_text(
        events.read_text().replace(
            'SCHEMA_VERSION = "0.2.0"',
            'SCHEMA_VERSION = "0.2.0"  # the spec version this set emits',
        )
    )
    tloop = n1 / "python" / "files" / "harness" / "core" / "loop.py"
    tloop.write_text(
        tloop.read_text().replace("Lesson: without a ceiling", "Lesson (0.1.1): without a ceiling")
    )
    head = git(out, "rev-parse", "HEAD")
    report = upgrade.upgrade(out, base=n1)
    assert report["branch"] == "harnesswalk/upgrade-0.1.1"
    # config.py carries the template version, so it changes with every set
    assert set(report["changed"]) == {
        "harness/core/events.py",
        "harness/core/loop.py",
        "harness/core/config.py",
    }
    assert (report["from"], report["to"]) == ("0.1.0", "0.1.1")
    assert report["conflicts"] == [] and report["codemods"] == ["manifest_lists_owned"]
    # The working tree and the current branch are untouched.
    assert git(out, "rev-parse", "HEAD") == head and git(out, "status", "--porcelain") == ""
    assert "the spec version this set emits" not in (out / "harness/core/events.py").read_text()
    show = lambda path: git(out, "show", f"{report['branch']}:{path}")  # noqa: E731
    assert "the spec version this set emits" in show("harness/core/events.py")
    merged = show("harness/core/loop.py")
    assert "Stopped: you interrupted the turn." in merged and "Lesson (0.1.1)" in merged
    assert show("prompts/system_prompt.md") == prompt.read_text()  # owned: never touched
    lock = yaml.safe_load(show("harness.lock"))
    assert lock["template_version"] == "0.1.1" and lock["codemods_applied"] == [
        "manifest_lists_owned"
    ]
    assert "owned" in yaml.safe_load(show("harness.yaml"))
    assert "Lesson (0.1.1)" in show(".harnesswalk/pristine/harness/core/loop.py")
    # A second upgrade to the same version is refused, not repeated.
    try:
        upgrade.upgrade(out, base=n1)
    except upgrade.UpgradeError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected a refusal")


def test_conflicts_are_left_as_markers_and_listed(tmp_path: Path) -> None:
    n = tmp_path / "n"
    shutil.copytree(build.TEMPLATES, n)
    out = tmp_path / "repo"
    build.init(GenManifest(profile="minimal", pack="helpdesk_lite"), out, base=n)
    loop = out / "harness" / "core" / "loop.py"
    loop.write_text(
        loop.read_text().replace("Lesson: without a ceiling", "Lesson (mine): without a ceiling")
    )
    git(out, "commit", "-qam", "mine")
    n1 = tmp_path / "n1"
    shutil.copytree(n, n1)
    (n1 / "python/manifest.yaml").write_text(
        (n1 / "python/manifest.yaml").read_text().replace("0.1.0", "0.2.0", 1)
    )
    t = n1 / "python/files/harness/core/loop.py"
    t.write_text(
        t.read_text().replace("Lesson: without a ceiling", "Lesson (theirs): without a ceiling")
    )
    report = upgrade.upgrade(out, base=n1)
    assert report["conflicts"] == ["harness/core/loop.py"]
    text = git(out, "show", f"{report['branch']}:harness/core/loop.py")
    assert "<<<<<<< yours" in text and ">>>>>>> template" in text
    assert subprocess.run(["git", "diff", "--quiet"], cwd=out).returncode == 0


def test_gen_diff_shows_edits_to_managed_files(tmp_path: Path) -> None:
    out = tmp_path / "repo"
    build.init(GenManifest(profile="minimal", pack="helpdesk_lite"), out, use_git=False)
    assert upgrade.diff(out) == ""
    loop = out / "harness" / "core" / "loop.py"
    loop.write_text(loop.read_text().replace("max_steps", "max_steps ", 1))
    text = upgrade.diff(out)
    assert "yours/harness/core/loop.py" in text and "template/harness/core/loop.py" in text
