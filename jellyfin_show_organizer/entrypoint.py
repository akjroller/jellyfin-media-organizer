from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from . import cli
from .apply_execution import ApplyExecutionError, prepare_apply
from .rollback_execution import (
    RollbackExecutionError,
    execute_rollback,
    prepare_rollback,
    rollback_token,
    total_rollback_members,
)
from .run_provenance import detect_source_revision

CommandHandler = Callable[[argparse.Namespace], int]
ROLLBACK_FAILED_EXIT = 31


def build_parser() -> argparse.ArgumentParser:
    """Extend the existing CLI with successful-run rollback."""

    parser = cli.build_parser()
    subparsers = cast(
        Any,
        next(action for action in parser._actions if hasattr(action, "add_parser")),
    )
    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Reverse one completed apply journal with a separate durable journal.",
        description=(
            "Verify one completed apply journal and atomically restore every approved "
            "member to its original source path in reverse operation order."
        ),
    )
    rollback_parser.add_argument("plan", type=Path)
    rollback_parser.add_argument("--preflight", type=Path, required=True)
    rollback_parser.add_argument("--run-provenance", type=Path, required=True)
    rollback_parser.add_argument("--apply-journal", type=Path, required=True)
    rollback_parser.add_argument("--source-root", type=Path, required=True)
    rollback_parser.add_argument("--destination-root", type=Path, required=True)
    rollback_parser.add_argument("--rollback-journal", type=Path)
    rollback_parser.add_argument("--approve-plan-sha256", required=True)
    rollback_parser.add_argument("--approve-review-session-sha256", required=True)
    rollback_parser.add_argument("--approve-source-revision", required=True)
    rollback_parser.add_argument(
        "--confirm-rollback",
        help=(
            "Exact confirmation token printed by --check-only. If omitted, an "
            "interactive terminal must type the displayed token."
        ),
    )
    rollback_parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Verify the completed apply journal and every live destination fingerprint "
            "without restoring any media."
        ),
    )
    rollback_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume only from the exact existing rollback journal.",
    )
    rollback_parser.add_argument("--json", action="store_true", dest="json_output")
    rollback_parser.set_defaults(handler=_run_rollback)
    return parser


def _run_rollback(args: argparse.Namespace) -> int:
    try:
        plan_path = cast(Path, args.plan).expanduser().resolve(strict=True)
        preflight_path = cast(Path, args.preflight).expanduser().resolve(strict=True)
        provenance_path = (
            cast(Path, args.run_provenance).expanduser().resolve(strict=True)
        )
        apply_journal_path = (
            cast(Path, args.apply_journal).expanduser().resolve(strict=True)
        )
        source_root = cast(Path, args.source_root).expanduser().resolve(strict=True)
        destination_root = (
            cast(Path, args.destination_root).expanduser().resolve(strict=True)
        )
        rollback_journal_arg = cast(Path | None, args.rollback_journal)
        rollback_journal_path = (
            rollback_journal_arg.expanduser().resolve(strict=False)
            if rollback_journal_arg is not None
            else None
        )
        if apply_journal_path in {plan_path, preflight_path, provenance_path}:
            raise RollbackExecutionError(
                "apply journal must be distinct from plan/provenance inputs"
            )
        if rollback_journal_path is not None and rollback_journal_path in {
            plan_path,
            preflight_path,
            provenance_path,
            apply_journal_path,
        }:
            raise RollbackExecutionError(
                "rollback journal must be distinct from every rollback input"
            )

        prepared_apply = prepare_apply(
            plan_path,
            preflight_path,
            provenance_path,
            approved_plan_sha256=cast(str, args.approve_plan_sha256).casefold(),
            approved_review_session_sha256=cast(
                str, args.approve_review_session_sha256
            ).casefold(),
            approved_source_revision=cast(str, args.approve_source_revision).casefold(),
        )
        current_revision = detect_source_revision()
        if current_revision.state != "git":
            raise RollbackExecutionError(
                "rollback requires a verifiable clean Git source revision"
            )
        if current_revision.dirty:
            raise RollbackExecutionError(
                "rollback refuses to run from a dirty source checkout"
            )
        if current_revision.commit != prepared_apply.source_revision:
            raise RollbackExecutionError(
                "running source revision does not match the approved plan"
            )

        prepared = prepare_rollback(prepared_apply, apply_journal_path)
        token = rollback_token(prepared, source_root, destination_root)
        check_only = bool(args.check_only)
        if check_only:
            result = execute_rollback(
                prepared,
                source_root,
                destination_root,
                rollback_journal_path=None,
                check_only=True,
                resume=False,
            )
        else:
            supplied = cast(str | None, args.confirm_rollback)
            if supplied is None:
                if not sys.stdin.isatty():
                    raise RollbackExecutionError(
                        "non-interactive rollback requires --confirm-rollback"
                    )
                print(
                    "Exact rollback approval required. This will atomically restore "
                    f"{total_rollback_members(prepared)} files in "
                    f"{len(prepared.prepared_apply.contract.groups)} operation groups."
                )
                print(f"Source root:       {source_root}")
                print(f"Destination root:  {destination_root}")
                print(f"Apply journal SHA: {prepared.apply_journal_sha256}")
                print(f"Confirmation token:\n{token}")
                supplied = input("Type the exact rollback confirmation token: ").strip()
            if supplied != token:
                raise RollbackExecutionError(
                    "rollback confirmation does not match the exact plan, review, "
                    "revision, apply journal, and roots"
                )
            result = execute_rollback(
                prepared,
                source_root,
                destination_root,
                rollback_journal_path=rollback_journal_path,
                check_only=False,
                resume=bool(args.resume),
            )
    except KeyboardInterrupt:
        print(
            "Rollback interrupted; leave restored source files in place and use "
            "--resume only with the exact rollback journal after checking the reported "
            "state.",
            file=sys.stderr,
        )
        return 130
    except (
        ApplyExecutionError,
        RollbackExecutionError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"Rollback failed safely: {exc}", file=sys.stderr)
        return ROLLBACK_FAILED_EXIT

    payload = result.to_dict()
    if bool(args.json_output):
        if check_only:
            payload["confirmation_token"] = token
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif check_only:
        print(
            "Rollback check ready: "
            f"plan={result.plan_sha256} groups={result.groups_total} "
            f"members={total_rollback_members(prepared)}"
        )
        print(f"Apply journal SHA-256: {prepared.apply_journal_sha256}")
        print(f"Confirmation token:\n{token}")
    else:
        print(
            "Rollback complete: "
            f"plan={result.plan_sha256} groups={result.groups_completed}/"
            f"{result.groups_total} restored={result.members_restored} "
            f"recovered={result.members_recovered} "
            f"journal={result.rollback_journal_path}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = cast(CommandHandler, args.handler)
    return handler(args)
