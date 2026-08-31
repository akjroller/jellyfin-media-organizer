"""Command-line boundary for the plan-first Jellyfin organizer."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the organizer CLI without reading settings or the filesystem."""
    parser = argparse.ArgumentParser(
        prog="jellyfin-show-organizer",
        description=(
            "Build and audit a Jellyfin television organization plan without "
            "moving media."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "plan",
        help="build a read-only organization plan (implementation pending)",
        description=(
            "Build a read-only organization plan. This scaffold does not inspect "
            "or change media yet."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse organizer arguments while all media behavior remains disabled."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "plan":
        parser.error("plan is not implemented; no files were read or changed")

    raise AssertionError(f"unexpected organizer command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
