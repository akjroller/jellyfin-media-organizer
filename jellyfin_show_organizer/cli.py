from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import cast

CommandHandler = Callable[[argparse.Namespace], int]
PLAN_NOT_IMPLEMENTED_EXIT = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone organizer command-line parser."""
    parser = argparse.ArgumentParser(
        prog="organizer",
        description=(
            "Plan-only Jellyfin show organization tooling. "
            "Media mutation is intentionally unavailable."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser(
        "plan",
        help="Report that the planning workflow is not implemented yet.",
        description=(
            "The organizer plan command is currently a scaffold. "
            "It does not produce a plan until the end-to-end planner is implemented."
        ),
    )
    plan_parser.set_defaults(handler=_run_plan)

    return parser


def _run_plan(_: argparse.Namespace) -> int:
    print(
        "Organizer plan is not implemented yet; "
        "no plan, report, cache, destination directory, or media output was created.",
        file=sys.stderr,
    )
    return PLAN_NOT_IMPLEMENTED_EXIT


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone organizer CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = cast(CommandHandler, args.handler)
    return handler(args)
