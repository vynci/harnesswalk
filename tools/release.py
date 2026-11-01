"""Release preparation for the owner. It never commits, tags or pushes.

    uv run python tools/release.py prep 0.1.0 [--date 2026-11-01] [--dry-run]
    uv run python tools/release.py notes 0.1.0       # the changelog section, for the GitHub Release
    uv run python tools/release.py check-tag v0.1.0  # exits 1 unless the tag names the project version

`prep` sets the version in `pyproject.toml`, `harnesswalk/__init__.py` and `uv.lock`, moves the
`Unreleased` section of `CHANGELOG.md` under a new `[VERSION] - DATE` heading (leaving an empty
`Unreleased` above it), updates the compare links at the bottom, and prints the commands that
commit, tag and push. `make release-prep VERSION=0.1.0` runs it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/vynci/harnesswalk"
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "harnesswalk" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
# PEP 440 final and pre-releases, the shapes a tag `v<version>` may take.
VERSION = re.compile(r"^\d+\.\d+\.\d+((a|b|rc)\d+)?$")
UNRELEASED = "## [Unreleased]"
EMPTY_UNRELEASED = "## [Unreleased]\n\nNothing yet.\n"


class ReleaseError(Exception):
    pass


def project_version() -> str:
    match = re.search(r'^version = "([^"]+)"$', PYPROJECT.read_text(encoding="utf-8"), re.M)
    if not match:
        raise ReleaseError("no version line in pyproject.toml")
    return match.group(1)


def set_versions(version: str) -> dict[Path, str]:
    """The new text of pyproject.toml and harnesswalk/__init__.py (not written yet)."""
    out: dict[Path, str] = {}
    for path, pattern, line in (
        (PYPROJECT, r'^version = "[^"]+"$', f'version = "{version}"'),
        (INIT, r'^__version__ = "[^"]+"$', f'__version__ = "{version}"'),
    ):
        text = path.read_text(encoding="utf-8")
        new, count = re.subn(pattern, line, text, count=1, flags=re.M)
        if count != 1:
            raise ReleaseError(f"no version line in {path.relative_to(ROOT)}")
        out[path] = new
    return out


def released_versions(text: str) -> list[str]:
    return re.findall(r"^## \[(\d[^\]]*)\]", text, re.M)


def move_unreleased(text: str, version: str, date: str) -> str:
    """CHANGELOG.md with `Unreleased` moved under `[version] - date`, links updated."""
    if UNRELEASED not in text:
        raise ReleaseError("CHANGELOG.md has no '## [Unreleased]' section")
    if version in released_versions(text):
        raise ReleaseError(f"CHANGELOG.md already has a [{version}] section")
    start = text.index(UNRELEASED)
    body_start = start + len(UNRELEASED)
    following = re.search(r"^## \[|^\[[^\]]+\]: ", text[body_start:], re.M)
    end = body_start + following.start() if following else len(text)
    body = text[body_start:end].strip("\n")
    if not body.strip() or body.strip() == "Nothing yet.":
        raise ReleaseError("the Unreleased section is empty: nothing to release")
    section = f"{EMPTY_UNRELEASED}\n## [{version}] - {date}\n\n{body}\n\n"
    text = text[:start] + section + text[end:].lstrip("\n")

    previous = [v for v in released_versions(text) if v != version]
    links = {
        "Unreleased": f"{REPO_URL}/compare/v{version}...HEAD",
        version: (
            f"{REPO_URL}/compare/v{previous[0]}...v{version}"
            if previous
            else f"{REPO_URL}/releases/tag/v{version}"
        ),
    }
    for name, url in links.items():
        line = f"[{name}]: {url}"
        pattern = re.compile(rf"^\[{re.escape(name)}\]: .*$", re.M)
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        else:
            # New links go right under the Unreleased link, newest first.
            anchor = re.search(r"^\[Unreleased\]: .*$", text, re.M)
            if anchor:
                text = text[: anchor.end()] + "\n" + line + text[anchor.end() :]
            else:
                text = text.rstrip("\n") + "\n\n" + line + "\n"
    return text


def section(text: str, version: str) -> str:
    """The body of one changelog section (`[version]` or `Unreleased`)."""
    heading = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n", text, re.M)
    if not heading:
        raise ReleaseError(f"CHANGELOG.md has no [{version}] section")
    rest = text[heading.end() :]
    following = re.search(r"^## \[|^\[[^\]]+\]: ", rest, re.M)
    return rest[: following.start() if following else len(rest)].strip("\n") + "\n"


def prep(version: str, date: str, dry_run: bool) -> int:
    if not VERSION.match(version):
        raise ReleaseError(f"{version!r} is not a version like 0.1.0 or 0.2.0rc1")
    current = project_version()
    files = set_versions(version)
    files[CHANGELOG] = move_unreleased(CHANGELOG.read_text(encoding="utf-8"), version, date)
    for path, text in files.items():
        print(f"{'would write' if dry_run else 'wrote'} {path.relative_to(ROOT)}")
        if not dry_run:
            path.write_text(text, encoding="utf-8")
    if not dry_run:
        # uv.lock records the project's own version; --offline keeps every pin as it is.
        subprocess.run(["uv", "lock", "--offline"], cwd=ROOT, check=True)
    print(f"\nversion {current} -> {version}; CHANGELOG.md has a [{version}] - {date} section.")
    print("Check the diff, run make test and make lint, then (the owner, by hand):\n")
    print(f'  git commit -am "chore(release): v{version}"')
    print(f'  git tag -a v{version} -m "harnesswalk {version}"')
    print(f"  git push origin main v{version}")
    print("\nThe tag starts .github/workflows/release.yml: PyPI, ghcr.io and the GitHub Release.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Release preparation (never commits or tags).")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prep", help="bump the version and move Unreleased in the changelog")
    p.add_argument("version")
    p.add_argument("--date", default=dt.datetime.now(dt.UTC).date().isoformat())
    p.add_argument("--dry-run", action="store_true")
    n = sub.add_parser("notes", help="print one changelog section")
    n.add_argument("version")
    t = sub.add_parser("check-tag", help="exit 1 unless the tag is v<project version>")
    t.add_argument("tag")
    args = ap.parse_args(argv)
    try:
        if args.command == "prep":
            return prep(args.version, args.date, args.dry_run)
        if args.command == "notes":
            print(section(CHANGELOG.read_text(encoding="utf-8"), args.version), end="")
            return 0
        version = project_version()
        if args.tag != f"v{version}":
            raise ReleaseError(f"tag {args.tag} does not match the project version {version}")
        if INIT.read_text(encoding="utf-8").count(f'__version__ = "{version}"') != 1:
            raise ReleaseError(f"harnesswalk/__init__.py does not say {version}")
        print(f"tag {args.tag} matches version {version}")
        return 0
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
