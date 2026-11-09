"""The generator's command: `harnesswalk init` (docs/generator.md)."""

from __future__ import annotations

import argparse
from pathlib import Path

from harnesswalk.gen import build
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
        choices=["ollama", "fake"],
        default="ollama",
        help="the real model next to the fake provider (default ollama)",
    )
    init.add_argument("--model", help="the Ollama model (default qwen3.5:4b)")
    init.add_argument("--transport", choices=["http", "cli"], default="http")
    init.add_argument("--storage", choices=["sqlite", "files"], default="sqlite")
    init.add_argument(
        "--release", choices=["file_door", "chain"], help="default: chain from governed up"
    )
    init.add_argument("--tune", action="store_true", help="add curate and tune (not generated yet)")
    init.add_argument("--out", required=True, help="the directory to write (must be empty)")
    init.add_argument("--force", action="store_true", help="replace a non-empty --out")
    init.add_argument("--no-git", action="store_true", help="do not git init and commit the output")


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
    if args.model:
        m.ollama_model = args.model
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
