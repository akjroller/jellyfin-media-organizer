from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from typing import cast

from . import __version__

CommandHandler = Callable[[argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone organizer command-line parser."""
    parser = argparse.ArgumentParser(
        prog="organizer",
        description=(
            "Plan-only Jellyfin show organization tooling. "
            "Media mutation is intentionally unavailable."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Jellyfin Media Organizer {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser(
        "plan",
        help="Initialize the read-only planning workflow.",
        description=(
            "Initialize the organizer planning workflow. "
            "Inventory scanning and provider matching are added in later issues."
        ),
    )
    plan_parser.set_defaults(handler=_run_plan)

    return parser


def _run_plan(_: argparse.Namespace) -> int:
    print(
        "Organizer plan scaffold is ready; "
        "it does not read, move, rename, or delete media files."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone organizer CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = cast(CommandHandler, args.handler)
    return handler(args)
