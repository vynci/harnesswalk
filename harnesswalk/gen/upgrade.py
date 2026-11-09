"""`harnesswalk upgrade`: a newer template set merged into a generated repo, on a new branch.

For every managed file (harness.lock `managed: true`) three versions meet: the pristine copy
(`.harnesswalk/pristine/`, what was generated), the new template rendered with the repo's own
`harness.yaml`, and the user's file as committed. Unchanged template: nothing to do. Untouched
user file: take the new one. Both changed: `git merge-file`, conflicts left as markers and
listed. Owned files are never read or written. Codemods keyed to the lock's template version
run after the merge (codemods.py). Everything is written in a temporary worktree on a new
branch and committed there: the user's working tree is never changed in place.
"""

from __future__ import annotations

import difflib
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from harnesswalk.gen import build, codemods
from harnesswalk.gen.manifest import GenManifest


class UpgradeError(RuntimeError):
    pass


def _load(repo: Path) -> tuple[GenManifest, dict[str, Any]]:
    if not (repo / "harness.lock").exists():
        raise UpgradeError(f"{repo} has no harness.lock: not a generated repo")
    lock = yaml.safe_load((repo / "harness.lock").read_text(encoding="utf-8"))
    manifest = GenManifest.from_dict(
        yaml.safe_load((repo / "harness.yaml").read_text(encoding="utf-8"))
    )
    return manifest, lock


def merge3(ours: str, base: str, theirs: str) -> tuple[str, bool]:
    """git merge-file on three texts: (merged, conflicted)."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            path = Path(tmp) / name
            path.write_text(text, encoding="utf-8")
            paths.append(str(path))
        done = subprocess.run(
            ["git", "merge-file", "-p", "-L", "yours", "-L", "pristine", "-L", "template", *paths],
            capture_output=True,
            text=True,
        )
        if done.returncode < 0 or done.returncode > 127:
            raise UpgradeError(done.stderr)
        return done.stdout, done.returncode > 0


def upgrade(
    repo: Path, *, branch: str | None = None, base: Path | None = None, root: Path | None = None
) -> dict[str, Any]:
    repo = repo.resolve()
    m, lock = _load(repo)
    old_version = lock["template_version"]
    meta, _ = build.template_set(m.template_set, base)
    plan = _plan_for(repo, m, root, base)
    branch = branch or f"harnesswalk/upgrade-{meta['version']}"
    if (
        subprocess.run(
            ["git", "rev-parse", "--verify", "-q", branch], cwd=repo, capture_output=True
        ).returncode
        == 0
    ):
        raise UpgradeError(f"branch {branch} already exists")
    changed, conflicts, added = [], [], []
    with tempfile.TemporaryDirectory(prefix="hw-upgrade-") as tmp:
        wt = Path(tmp) / "wt"
        build.git(repo, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
        try:
            for path in plan["managed"]:
                new = plan["files"][path].decode()
                pristine_file = wt / ".harnesswalk" / "pristine" / path
                pristine = (
                    pristine_file.read_text(encoding="utf-8") if pristine_file.exists() else None
                )
                user_file = wt / path
                user = user_file.read_text(encoding="utf-8") if user_file.exists() else None
                if pristine == new:
                    continue
                if user is None or pristine is None:
                    merged, conflict = new, False
                    added.append(path)
                elif user == pristine:
                    merged, conflict = new, False
                else:
                    merged, conflict = merge3(user, pristine, new)
                user_file.parent.mkdir(parents=True, exist_ok=True)
                user_file.write_text(merged, encoding="utf-8")
                pristine_file.parent.mkdir(parents=True, exist_ok=True)
                pristine_file.write_text(new, encoding="utf-8")
                changed.append(path)
                if conflict:
                    conflicts.append(path)
            applied = codemods.run(wt, lock["template_version"], meta["version"])
            lock["template_version"], lock["spec_version"] = meta["version"], meta["spec_version"]
            lock["codemods_applied"] = list(lock.get("codemods_applied") or []) + applied
            for path in changed:
                lock["files"][path] = {"sha256": build.sha(plan["files"][path]), "managed": True}
            (wt / "harness.lock").write_text(
                "# Written by harnesswalk; do not edit by hand.\n"
                + yaml.safe_dump(lock, sort_keys=False),
                encoding="utf-8",
            )
            build.git(wt, "add", "-A")
            message = f"harnesswalk upgrade: template set {meta['version']}"
            if conflicts:
                message += f" ({len(conflicts)} file(s) with conflict markers)"
            build.git(wt, "commit", "-q", "-m", message)
        finally:
            build.git(repo, "worktree", "remove", "--force", str(wt))
    return {
        "branch": branch,
        "from": old_version,
        "to": meta["version"],
        "changed": changed,
        "conflicts": conflicts,
        "added": added,
        "codemods": applied,
    }


def _plan_for(repo: Path, m: GenManifest, root: Path | None, base: Path | None) -> dict[str, Any]:
    """The managed files a fresh render of this repo's manifest gives (templates only: the
    data files, the pack and the owned folders are never re-rendered)."""
    data = yaml.safe_load((repo / "harness.yaml").read_text(encoding="utf-8"))
    card_path = repo / "packs" / m.pack / "pack.yaml"
    card = yaml.safe_load(card_path.read_text(encoding="utf-8")) if card_path.exists() else {}
    rendered = build.render_set(
        m, set(data["generated_nodes"]), (card or {}).get("title") or m.pack, base, root
    )
    return {
        "files": {p: t.encode() for p, (t, _) in rendered.items()},
        "managed": sorted(p for p, (_, managed) in rendered.items() if managed),
    }


def describe(report: dict[str, Any]) -> str:
    lines = [
        f"branch {report['branch']}: template set {report['to']}",
        f"merged {len(report['changed'])} managed file(s)"
        + (f": {', '.join(report['changed'])}" if report["changed"] else ""),
    ]
    if report["conflicts"]:
        lines.append("conflicts (markers left in place): " + ", ".join(report["conflicts"]))
    if report["codemods"]:
        lines.append("codemods: " + ", ".join(report["codemods"]))
    lines.append("review with git diff main.." + report["branch"] + ", then merge it yourself")
    return "\n".join(lines)


def diff(repo: Path, root: Path | None = None) -> str:
    """Managed files that differ from their pristine copy (your edits) and from a fresh render."""
    repo = repo.resolve()
    m, _ = _load(repo)
    plan = _plan_for(repo, m, root, None)
    out = []
    for path in plan["managed"]:
        mine = (repo / path).read_text(encoding="utf-8") if (repo / path).exists() else ""
        fresh = plan["files"][path].decode()
        if mine != fresh:
            out += difflib.unified_diff(
                fresh.splitlines(True), mine.splitlines(True), f"template/{path}", f"yours/{path}"
            )
    return "".join(out)
