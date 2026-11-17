"""The generator's commands: `harnesswalk init`, `harnesswalk upgrade`, `harnesswalk gen diff`."""

from __future__ import annotations

import argparse
from pathlib import Path

from harnesswalk.gen import build, upgrade
from harnesswalk.gen.manifest import GenManifest, ManifestError


def add_parsers(sub: argparse._SubParsersAction) -> None:
    init = sub.add_parser("init", help="generate a harness repository you own (docs/generator.md)")
    init.add_argument(
        "--profile", help="minimal, guarded, grounded, remembered or governed (default: the pack's)"
    )
    init.add_argument(
        "--pack", required=True, help="a pack id under datasets/, or a pack directory"
    )
    init.add_argument(
        "--provider",
        choices=["ollama", "anthropic", "fake"],
        default="ollama",
        help="the real model next to the fake provider (default ollama)",
    )
    init.add_argument("--model", help="the provider's model (default qwen3.5:4b, claude-haiku-5-5)")
    init.add_argument("--transport", choices=["http", "cli"], default="http")
    init.add_argument("--storage", choices=["sqlite", "files"], default="sqlite")
    init.add_argument(
        "--release", choices=["file_door", "chain"], help="default: chain from governed up"
    )
    init.add_argument("--tune", action="store_true", help="emit the tune node (needs the P3 spec)")
    init.add_argument("--out", required=True, help="the directory to write (must be empty)")
    init.add_argument("--force", action="store_true", help="replace a non-empty --out")
    init.add_argument("--no-git", action="store_true", help="do not git init and commit the output")

    up = sub.add_parser(
        "upgrade", help="merge a newer template set into a generated repo, on a branch"
    )
    up.add_argument("repo", help="the generated repository")
    up.add_argument("--branch", help="the branch to write (default harnesswalk/upgrade-<version>)")

    gen = sub.add_parser("gen", help="generator helpers")
    gen.add_argument("action", choices=["diff"])
    gen.add_argument("repo", help="the generated repository")


def run_init(args: argparse.Namespace) -> int:
    providers = ["fake"] if args.provider == "fake" else ["fake", args.provider]
    m = GenManifest(
        profile=args.profile or "",
        pack=args.pack,
        providers=providers,
        transport=args.transport,
        storage=args.storage,
        release=args.release or "",
        tune=args.tune,
    )
    if args.model and args.provider == "ollama":
        m.ollama_model = args.model
    elif args.model and args.provider == "anthropic":
        m.anthropic_model = args.model
    try:
        done = build.init(m, Path(args.out), force=args.force, use_git=not args.no_git)
    except (ManifestError, ValueError, FileExistsError) as exc:
        print(f"error: {exc}")
        return 2
    lines = done["lines"]
    print(
        f"wrote {done['out']}: profile {m.profile}, pack {m.pack}, release {m.release}, "
        f"{len(done['files'])} files, {lines['harness']} lines of Python in harness/ "
        f"({lines['python']} with tests and evals)"
    )
    print(
        "next: cd there, make setup && make eval && make conformance;"
        " GENERATION_NOTES.md lists what was not mapped"
    )
    return 0


def run_upgrade(args: argparse.Namespace) -> int:
    try:
        report = upgrade.upgrade(Path(args.repo), branch=args.branch)
    except (upgrade.UpgradeError, FileNotFoundError) as exc:
        print(f"error: {exc}")
        return 2
    print(upgrade.describe(report))
    return 1 if report["conflicts"] else 0


def run_gen(args: argparse.Namespace) -> int:
    print(
        upgrade.diff(Path(args.repo)) or "no differences: the managed files match the template set"
    )
    return 0
