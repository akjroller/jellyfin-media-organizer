"""Command adapters for empty-source-folder cleanup after a completed apply."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from .prune_execution import (
    PruneError,
    PruneResult,
    execute_prune,
    plan_prune,
    restore_pruned_dirs,
)

PRUNE_FAILED_EXIT = 35
PRUNE_RESTORE_FAILED_EXIT = 36


def register_prune_commands(subparsers: Any) -> None:
    prune = subparsers.add_parser(
        "prune-empty-dirs",
        help="Remove source folders a completed apply left empty.",
        description=(
            "List, then remove, the source folders that held files moved by one "
            "completed apply journal and are now completely empty. Folders are "
            "removed deepest first and only while empty; media is never touched. "
            "Run with --check-only first."
        ),
    )
    prune.add_argument("--source-root", type=Path, required=True)
    prune.add_argument("--apply-journal", type=Path, required=True)
    prune.add_argument(
        "--journal",
        type=Path,
        help="New prune journal outside the source root (required to remove).",
    )
    prune.add_argument(
        "--confirm-prune",
        help=(
            "Exact token printed by --check-only. If omitted, an interactive "
            "terminal must type the displayed token."
        ),
    )
    prune.add_argument(
        "--check-only",
        action="store_true",
        help="List the folders that would be removed without removing any.",
    )
    prune.add_argument("--json", action="store_true", dest="json_output")
    prune.set_defaults(handler=_run_prune)

    restore = subparsers.add_parser(
        "restore-pruned-dirs",
        help="Recreate the folders a prune journal removed (before a rollback).",
        description=(
            "Recreate every empty folder recorded in one prune journal, parents "
            "first. Existing folders are left alone, so this is safe to repeat. "
            "Run it before jmo rollback if you pruned after the apply."
        ),
    )
    restore.add_argument("--source-root", type=Path, required=True)
    restore.add_argument("--prune-journal", type=Path, required=True)
    restore.add_argument("--json", action="store_true", dest="json_output")
    restore.set_defaults(handler=_run_restore)


def _print_result(result: PruneResult, token: str | None, json_output: bool) -> None:
    if json_output:
        payload = result.to_dict()
        if token is not None:
            payload["confirmation_token"] = token
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    if result.check_only:
        if not result.directories:
            print("No empty source folders to remove.")
            return
        print(f"Empty source folders that would be removed: {len(result.directories)}")
        for directory in result.directories:
            print(f"  {directory}")
        print(f"Confirmation token:\n{token}")
        return
    print(
        f"Removed {len(result.removed)} empty source folder(s); "
        f"journal={result.journal_path}"
    )


def _run_prune(args: argparse.Namespace) -> int:
    apply_journal = cast(Path, args.apply_journal).expanduser()
    try:
        plan = plan_prune(cast(Path, args.source_root), apply_journal)
        if bool(args.check_only) or not plan.directories:
            _print_result(
                PruneResult(
                    directories=plan.directories,
                    removed=(),
                    journal_path=None,
                    check_only=True,
                ),
                plan.token if plan.directories else None,
                bool(args.json_output),
            )
            return 0
        journal = cast(Path | None, args.journal)
        if journal is None:
            raise PruneError("removing folders requires --journal")
        supplied = cast(str | None, args.confirm_prune)
        if supplied is None:
            if not sys.stdin.isatty():
                raise PruneError("non-interactive prune requires --confirm-prune")
            print(f"This will remove {len(plan.directories)} empty folder(s):")
            for directory in plan.directories:
                print(f"  {directory}")
            print(f"Confirmation token:\n{plan.token}")
            supplied = input("Type the exact confirmation token: ").strip()
        result = execute_prune(
            plan, apply_journal, confirm=supplied, journal_path=journal
        )
    except (PruneError, OSError, ValueError) as exc:
        print(f"Prune failed safely: {exc}", file=sys.stderr)
        return PRUNE_FAILED_EXIT
    _print_result(result, None, bool(args.json_output))
    return 0


def _run_restore(args: argparse.Namespace) -> int:
    try:
        restored = restore_pruned_dirs(
            cast(Path, args.source_root), cast(Path, args.prune_journal)
        )
    except (PruneError, OSError, ValueError) as exc:
        print(f"Restore failed safely: {exc}", file=sys.stderr)
        return PRUNE_RESTORE_FAILED_EXIT
    if bool(args.json_output):
        print(
            json.dumps(
                {"schema_version": 1, "restored": list(restored)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        print(f"Recreated {len(restored)} folder(s).")
    return 0
