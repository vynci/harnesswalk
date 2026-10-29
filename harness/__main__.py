"""Entry point for ``python -m harness`` and the ``harnesswalk`` script."""

import sys

from harness import SPEC_VERSION, __version__


def main() -> int:
    print(
        f"harnesswalk {__version__} (spec {SPEC_VERSION}): pre-alpha, P0 spec only.\n"
        "There is no runtime yet. The gateway, loop and fake provider arrive in P1.\n"
        "Read spec/harness.md to see what will run here."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
