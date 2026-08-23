"""CLI entry point for Awn."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .core import Awn


def main(argv: list[str] | None = None) -> int:
    """Run the Awn CLI."""
    parser = argparse.ArgumentParser(
        prog="awn",
        description="Awn (عَوْن) — a versatile and intelligent aide.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"awn {__version__}",
    )
    parser.add_argument(
        "command",
        nargs="?",
        help="Command to run (non-interactive mode).",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments for the command.",
    )

    ns = parser.parse_args(argv)
    aide = Awn()

    if ns.command:
        full_input = " ".join([ns.command] + ns.args)
        result = aide.handle(full_input)
        if result:
            print(result)
        return 0

    aide.run_interactive()
    return 0


if __name__ == "__main__":
    sys.exit(main())
