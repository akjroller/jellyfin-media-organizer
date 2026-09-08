from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from .apply_execution import ApplyExecutionError, PreparedApply, prepare_apply
from .quarantine_contract import (
    QuarantineContractError,
    derive_quarantine_plan,
    render_quarantine_plan,
)
from .quarantine_execution import (
    QuarantineExecutionError,
    execute_quarantine,
    execute_quarantine_restore,
    prepare_quarantine,
    prepare_quarantine_restore,
    quarantine_restore_token,
    quarantine_token,
)
from .review_session import atomic_write_new
from .run_provenance import detect_source_revision

QUARANTINE_FAILED_EXIT = 32
QUARANTINE_RESTORE_FAILED_EXIT = 33
QUARANTINE_PLAN_FAILED_EXIT = 34


def _add_approval_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plan", type=Path)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--run-provenance", type=Path, required=True)
    parser.add_argument("--approve-plan-sha256", required=True)
    parser.add_argument("--approve-review-session-sha256", required=True)
    parser.add_argument("--approve-source-revision", required=True)


def _add_quarantine_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--quarantine-root", type=Path, required=True)


def register_quarantine_commands(subparsers: Any) -> None:
    plan_parser = subparsers.add_parser(
        "quarantine-plan",
        help="Create an immutable plan containing reviewed duplicate losers only.",
        description=(
            "Derive a no-delete duplicate quarantine artifact from one exact complete "
            "reviewed plan without touching media files."
        ),
    )
    _add_approval_args(plan_parser)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--json", action="store_true", dest="json_output")
    plan_parser.set_defaults(handler=_run_quarantine_plan)

    quarantine_parser = subparsers.add_parser(
        "quarantine",
        help="Move reviewed duplicate losers to a reversible same-volume quarantine.",
        description=(
            "Consume an exact immutable quarantine plan. Check-only accepts winners in "
            "their reviewed pre-apply or organized state; mutation requires every winner "
            "to be verified at its approved organized destination."
        ),
    )
    _add_approval_args(quarantine_parser)
    _add_quarantine_roots(quarantine_parser)
    quarantine_parser.add_argument("--quarantine-plan", type=Path, required=True)
    quarantine_parser.add_argument("--approve-quarantine-plan-sha256", required=True)
    quarantine_parser.add_argument("--journal", type=Path)
    quarantine_parser.add_argument("--confirm-quarantine")
    quarantine_parser.add_argument("--check-only", action="store_true")
    quarantine_parser.add_argument("--resume", action="store_true")
    quarantine_parser.add_argument("--json", action="store_true", dest="json_output")
    quarantine_parser.set_defaults(handler=_run_quarantine)

    restore_parser = subparsers.add_parser(
        "quarantine-restore",
        help="Restore one completed duplicate quarantine run exactly.",
        description=(
            "Verify one completed quarantine journal and atomically restore every loser "
            "to its original reviewed source path without overwriting anything."
        ),
    )
    _add_approval_args(restore_parser)
    _add_quarantine_roots(restore_parser)
    restore_parser.add_argument("--quarantine-plan", type=Path, required=True)
    restore_parser.add_argument("--approve-quarantine-plan-sha256", required=True)
    restore_parser.add_argument("--quarantine-journal", type=Path, required=True)
    restore_parser.add_argument("--restore-journal", type=Path)
    restore_parser.add_argument("--confirm-restore")
    restore_parser.add_argument("--check-only", action="store_true")
    restore_parser.add_argument("--resume", action="store_true")
    restore_parser.add_argument("--json", action="store_true", dest="json_output")
    restore_parser.set_defaults(handler=_run_quarantine_restore)


def _paths_and_prepared(args: argparse.Namespace) -> tuple[Path, Path, Path, PreparedApply]:
    plan_path = cast(Path, args.plan).expanduser().resolve(strict=True)
    preflight_path = cast(Path, args.preflight).expanduser().resolve(strict=True)
    provenance_path = cast(Path, args.run_provenance).expanduser().resolve(strict=True)
    prepared = prepare_apply(
        plan_path,
        preflight_path,
        provenance_path,
        approved_plan_sha256=cast(str, args.approve_plan_sha256).casefold(),
        approved_review_session_sha256=cast(
            str, args.approve_review_session_sha256
        ).casefold(),
        approved_source_revision=cast(str, args.approve_source_revision).casefold(),
    )
    current = detect_source_revision()
    if current.state != "git":
        raise QuarantineExecutionError(
            "quarantine operations require a verifiable clean Git source revision"
        )
    if current.dirty:
        raise QuarantineExecutionError(
            "quarantine operations refuse to run from a dirty source checkout"
        )
    if current.commit != prepared.source_revision:
        raise QuarantineExecutionError(
            "running source revision does not match the approved plan"
        )
    return plan_path, preflight_path, provenance_path, prepared


def _load_manifest(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QuarantineExecutionError("could not load valid plan manifest") from exc


def _load_bytes(path: Path, label: str) -> bytes:
    if path.is_symlink():
        raise QuarantineExecutionError(f"{label} cannot be a symlink")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise QuarantineExecutionError(f"could not read {label}") from exc


def _roots(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    return (
        cast(Path, args.source_root).expanduser().resolve(strict=True),
        cast(Path, args.destination_root).expanduser().resolve(strict=True),
        cast(Path, args.quarantine_root).expanduser().resolve(strict=True),
    )


def _run_quarantine_plan(args: argparse.Namespace) -> int:
    try:
        plan_path, _, _, prepared = _paths_and_prepared(args)
        manifest = _load_manifest(plan_path)
        quarantine_plan = derive_quarantine_plan(manifest, prepared)
        output = cast(Path, args.output).expanduser().resolve(strict=False)
        atomic_write_new(output, render_quarantine_plan(quarantine_plan))
    except KeyboardInterrupt:
        print("Quarantine-plan creation interrupted; no media was changed.", file=sys.stderr)
        return 130
    except (
        ApplyExecutionError,
        QuarantineContractError,
        QuarantineExecutionError,
        FileExistsError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"Quarantine-plan creation failed safely: {exc}", file=sys.stderr)
        return QUARANTINE_PLAN_FAILED_EXIT

    payload = {
        "schema_version": quarantine_plan.schema_version,
        "quarantine_plan_sha256": quarantine_plan.sha256,
        "plan_sha256": quarantine_plan.plan_sha256,
        "review_session_sha256": quarantine_plan.review_session_sha256,
        "source_revision": quarantine_plan.source_revision,
        "groups": len(quarantine_plan.groups),
        "members": sum(len(group.members) for group in quarantine_plan.groups),
        "output": str(output),
    }
    if bool(args.json_output):
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "Quarantine plan created: "
            f"groups={payload['groups']} members={payload['members']} "
            f"sha256={quarantine_plan.sha256} output={output}"
        )
    return 0


def _prepare_quarantine_from_args(args: argparse.Namespace):
    plan_path, _, _, prepared_apply = _paths_and_prepared(args)
    manifest = _load_manifest(plan_path)
    quarantine_plan_path = (
        cast(Path, args.quarantine_plan).expanduser().resolve(strict=True)
    )
    prepared = prepare_quarantine(
        prepared_apply,
        manifest,
        _load_bytes(quarantine_plan_path, "quarantine plan"),
        approved_quarantine_plan_sha256=cast(
            str, args.approve_quarantine_plan_sha256
        ).casefold(),
    )
    return prepared


def _run_quarantine(args: argparse.Namespace) -> int:
    try:
        prepared = _prepare_quarantine_from_args(args)
        source_root, organized_root, quarantine_root = _roots(args)
        token = quarantine_token(
            prepared, source_root, organized_root, quarantine_root
        )
        check_only = bool(args.check_only)
        journal_arg = cast(Path | None, args.journal)
        journal_path = (
            journal_arg.expanduser().resolve(strict=False)
            if journal_arg is not None
            else None
        )
        if check_only:
            result = execute_quarantine(
                prepared,
                source_root,
                organized_root,
                quarantine_root,
                journal_path=None,
                check_only=True,
                resume=False,
            )
        else:
            supplied = cast(str | None, args.confirm_quarantine)
            if supplied is None:
                if not sys.stdin.isatty():
                    raise QuarantineExecutionError(
                        "non-interactive quarantine requires --confirm-quarantine"
                    )
                print(
                    "Exact quarantine approval required. This will atomically move "
                    f"{sum(len(group.members) for group in prepared.plan.groups)} files "
                    "outside the library without deleting them."
                )
                print(f"Source root:      {source_root}")
                print(f"Organized root:   {organized_root}")
                print(f"Quarantine root:  {quarantine_root}")
                print(f"Quarantine SHA:   {prepared.plan.sha256}")
                print(f"Confirmation token:\n{token}")
                supplied = input("Type the exact quarantine confirmation token: ").strip()
            if supplied != token:
                raise QuarantineExecutionError(
                    "quarantine confirmation does not match the exact artifact, "
                    "review, revision, and roots"
                )
            result = execute_quarantine(
                prepared,
                source_root,
                organized_root,
                quarantine_root,
                journal_path=journal_path,
                check_only=False,
                resume=bool(args.resume),
            )
    except KeyboardInterrupt:
        print(
            "Quarantine interrupted; leave source/quarantine files in place and use "
            "--resume only with the exact journal after inspecting reported state.",
            file=sys.stderr,
        )
        return 130
    except (
        ApplyExecutionError,
        QuarantineContractError,
        QuarantineExecutionError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"Quarantine failed safely: {exc}", file=sys.stderr)
        return QUARANTINE_FAILED_EXIT

    payload = result.to_dict()
    if bool(args.json_output):
        if check_only:
            payload["confirmation_token"] = token
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif check_only:
        print(
            "Quarantine check ready: "
            f"groups={result.groups_total} members={result.members_total} "
            f"winners_preapply={result.winners_preapply} "
            f"winners_organized={result.winners_organized}"
        )
        print(f"Quarantine plan SHA-256: {prepared.plan.sha256}")
        print(f"Confirmation token:\n{token}")
    else:
        print(
            "Quarantine complete: "
            f"groups={result.groups_completed}/{result.groups_total} "
            f"moved={result.members_moved} recovered={result.members_recovered} "
            f"journal={result.journal_path}"
        )
    return 0


def _run_quarantine_restore(args: argparse.Namespace) -> int:
    try:
        prepared = _prepare_quarantine_from_args(args)
        source_root, organized_root, quarantine_root = _roots(args)
        quarantine_journal = (
            cast(Path, args.quarantine_journal).expanduser().resolve(strict=True)
        )
        restore = prepare_quarantine_restore(prepared, quarantine_journal)
        token = quarantine_restore_token(
            restore, source_root, organized_root, quarantine_root
        )
        check_only = bool(args.check_only)
        journal_arg = cast(Path | None, args.restore_journal)
        restore_journal = (
            journal_arg.expanduser().resolve(strict=False)
            if journal_arg is not None
            else None
        )
        if check_only:
            result = execute_quarantine_restore(
                restore,
                source_root,
                organized_root,
                quarantine_root,
                restore_journal_path=None,
                check_only=True,
                resume=False,
            )
        else:
            supplied = cast(str | None, args.confirm_restore)
            if supplied is None:
                if not sys.stdin.isatty():
                    raise QuarantineExecutionError(
                        "non-interactive quarantine restore requires --confirm-restore"
                    )
                print(
                    "Exact quarantine restore approval required. This will atomically "
                    f"restore {sum(len(group.members) for group in prepared.plan.groups)} "
                    "files to their reviewed source paths."
                )
                print(f"Source root:             {source_root}")
                print(f"Quarantine root:         {quarantine_root}")
                print(f"Quarantine journal SHA:  {restore.quarantine_journal_sha256}")
                print(f"Confirmation token:\n{token}")
                supplied = input("Type the exact restore confirmation token: ").strip()
            if supplied != token:
                raise QuarantineExecutionError(
                    "quarantine restore confirmation does not match the exact journal, "
                    "artifact, revision, and roots"
                )
            result = execute_quarantine_restore(
                restore,
                source_root,
                organized_root,
                quarantine_root,
                restore_journal_path=restore_journal,
                check_only=False,
                resume=bool(args.resume),
            )
    except KeyboardInterrupt:
        print(
            "Quarantine restore interrupted; leave restored files in place and use "
            "--resume only with the exact restore journal after inspecting state.",
            file=sys.stderr,
        )
        return 130
    except (
        ApplyExecutionError,
        QuarantineContractError,
        QuarantineExecutionError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"Quarantine restore failed safely: {exc}", file=sys.stderr)
        return QUARANTINE_RESTORE_FAILED_EXIT

    payload = result.to_dict()
    if bool(args.json_output):
        if check_only:
            payload["confirmation_token"] = token
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif check_only:
        print(
            "Quarantine restore check ready: "
            f"groups={result.groups_total} members={result.members_total}"
        )
        print(f"Quarantine journal SHA-256: {restore.quarantine_journal_sha256}")
        print(f"Confirmation token:\n{token}")
    else:
        print(
            "Quarantine restore complete: "
            f"groups={result.groups_completed}/{result.groups_total} "
            f"restored={result.members_restored} recovered={result.members_recovered} "
            f"journal={result.journal_path}"
        )
    return 0
