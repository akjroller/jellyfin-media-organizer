from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from .overrides import load_overrides

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
            "Planning is not implemented yet, so this command does not produce a plan."
        ),
    )
    plan_parser.set_defaults(handler=_run_plan)

    overrides_parser = subparsers.add_parser(
        "overrides",
        help="Inspect explicitly selected local override files.",
        description=(
            "Validate local planning overrides without reading or mutating media. "
            "Override files are never loaded implicitly by this command."
        ),
    )
    overrides_subparsers = overrides_parser.add_subparsers(
        dest="overrides_command",
        required=True,
    )
    validate_parser = overrides_subparsers.add_parser(
        "validate",
        help="Validate one explicitly selected override TOML file.",
    )
    validate_parser.add_argument("path", type=Path)
    validate_parser.set_defaults(handler=_run_overrides_validate)

    return parser


def _run_plan(_: argparse.Namespace) -> int:
    print(
        "Organizer plan is not implemented yet; "
        "no plan, report, cache, destination directory, or media output was created.",
        file=sys.stderr,
    )
    return PLAN_NOT_IMPLEMENTED_EXIT


def _run_overrides_validate(args: argparse.Namespace) -> int:
    path = cast(Path, args.path)
    try:
        catalog = load_overrides(path)
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        print(
            f"Override file invalid: cannot read file ({detail})",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        print(f"Override file invalid: {exc}", file=sys.stderr)
        return 2

    print(
        "Override file valid: "
        f"schema={catalog.schema_version} "
        f"shows={len(catalog.shows)} "
        f"snapshot={catalog.snapshot_id}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone organizer CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = cast(CommandHandler, args.handler)
    return handler(args)
