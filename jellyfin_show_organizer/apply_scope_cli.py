from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from . import cli
from .apply_execution import (
    ApplyExecutionError,
    PreparedApply,
    approval_token,
    execute_apply,
    prepare_apply,
    total_moving_members,
)
from .apply_scope import (
    ApplyScopeError,
    create_apply_scope,
    prepare_scoped_apply,
    write_apply_scope,
)
from .apply_validation import ApplyFilesystemError, validate_apply_roots
from .run_provenance import detect_source_revision


def _scope_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope",
        type=Path,
        help="Immutable canary apply-scope artifact selecting explicit operation groups.",
    )
    parser.add_argument(
        "--approve-scope-sha256",
        help="Exact SHA-256 of the supplied immutable apply-scope artifact.",
    )


def register_apply_scope_commands(subparsers: Any) -> None:
    """Add scope creation and optional scoped apply to the public entry point."""

    apply_parser = cast(argparse.ArgumentParser, subparsers.choices["apply"])
    _scope_args(apply_parser)
    apply_parser.set_defaults(handler=_run_apply)

    scope_parser = subparsers.add_parser(
        "apply-scope",
        help="Create immutable explicit operation-group scopes for canary apply.",
    )
    scope_subparsers = scope_parser.add_subparsers(dest="scope_command", required=True)
    create_parser = scope_subparsers.add_parser(
        "create",
        help="Create one canonical canary scope from explicitly named group IDs.",
        description=(
            "Bind an explicit non-empty proper subset of operation groups to one exact "
            "fully reviewed plan. No first-N, random, or implicit selection exists."
        ),
    )
    create_parser.add_argument("plan", type=Path)
    create_parser.add_argument("--preflight", type=Path, required=True)
    create_parser.add_argument("--run-provenance", type=Path, required=True)
    create_parser.add_argument("--source-root", type=Path, required=True)
    create_parser.add_argument("--destination-root", type=Path, required=True)
    create_parser.add_argument("--approve-plan-sha256", required=True)
    create_parser.add_argument("--approve-review-session-sha256", required=True)
    create_parser.add_argument("--approve-source-revision", required=True)
    create_parser.add_argument(
        "--group-id",
        action="append",
        required=True,
        dest="group_ids",
        help="Explicit approved operation-group ID. Repeat for every canary group.",
    )
    create_parser.add_argument("--output", type=Path, required=True)
    create_parser.add_argument("--json", action="store_true", dest="json_output")
    create_parser.set_defaults(handler=_run_scope_create)


def bind_optional_scope(args: argparse.Namespace, prepared: PreparedApply) -> PreparedApply:
    """Bind both scope arguments or require neither; never infer a scope."""

    scope_arg = cast(Path | None, getattr(args, "scope", None))
    approved = cast(str | None, getattr(args, "approve_scope_sha256", None))
    if (scope_arg is None) != (approved is None):
        raise ApplyScopeError(
            "--scope and --approve-scope-sha256 must be supplied together"
        )
    if scope_arg is None:
        return prepared
    scope_path = scope_arg.expanduser().resolve(strict=True)
    try:
        payload = scope_path.read_bytes()
    except OSError as exc:
        raise ApplyScopeError("apply scope artifact is unreadable") from exc
    assert approved is not None
    return prepare_scoped_apply(
        prepared,
        payload,
        approved_scope_sha256=approved.casefold(),
    )


def _current_revision_matches(prepared: PreparedApply, *, operation: str) -> None:
    revision = detect_source_revision()
    if revision.state != "git":
        raise ApplyExecutionError(
            f"{operation} requires a verifiable clean Git source revision"
        )
    if revision.dirty:
        raise ApplyExecutionError(f"{operation} refuses to run from a dirty source checkout")
    if revision.commit != prepared.source_revision:
        raise ApplyExecutionError(
            f"running source revision does not match the approved {operation} plan"
        )


def _run_scope_create(args: argparse.Namespace) -> int:
    try:
        plan_path = cast(Path, args.plan).expanduser().resolve(strict=True)
        preflight_path = cast(Path, args.preflight).expanduser().resolve(strict=True)
        provenance_path = cast(Path, args.run_provenance).expanduser().resolve(strict=True)
        source_root, destination_root = validate_apply_roots(
            cast(Path, args.source_root).expanduser(),
            cast(Path, args.destination_root).expanduser(),
        )
        prepared = prepare_apply(
            plan_path,
            preflight_path,
            provenance_path,
            approved_plan_sha256=cast(str, args.approve_plan_sha256).casefold(),
            approved_review_session_sha256=cast(
                str, args.approve_review_session_sha256
            ).casefold(),
            approved_source_revision=cast(str, args.approve_source_revision).casefold(),
            separate_roots=source_root != destination_root,
        )
        _current_revision_matches(prepared, operation="apply-scope creation")
        group_ids = tuple(cast(list[str], args.group_ids))
        scope = create_apply_scope(prepared, group_ids)

        output = cast(Path, args.output).expanduser().resolve(strict=False)
        if output in {plan_path, preflight_path, provenance_path}:
            raise ApplyScopeError("apply scope output must be distinct from its inputs")
        parent = output.parent.resolve(strict=True)
        if parent == source_root or parent.is_relative_to(source_root):
            raise ApplyScopeError("apply scope output must be outside the source root")
        if parent == destination_root or parent.is_relative_to(destination_root):
            raise ApplyScopeError("apply scope output must be outside the destination root")
        write_apply_scope(output, scope)
    except (
        ApplyExecutionError,
        ApplyFilesystemError,
        ApplyScopeError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"Apply scope creation failed safely: {exc}", file=sys.stderr)
        return cli.APPLY_FAILED_EXIT

    payload = {
        "schema_version": 1,
        "apply_scope_sha256": scope.sha256,
        "plan_sha256": scope.plan_sha256,
        "review_session_sha256": scope.review_session_sha256,
        "source_revision": scope.source_revision,
        "groups_total": len(scope.group_ids),
        "output": str(output),
    }
    if bool(args.json_output):
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "Apply scope created: "
            f"groups={len(scope.group_ids)} sha256={scope.sha256} output={output}"
        )
    return 0


def _run_apply(args: argparse.Namespace) -> int:
    scope_arg = cast(Path | None, getattr(args, "scope", None))
    approved_scope = cast(str | None, getattr(args, "approve_scope_sha256", None))
    if scope_arg is None and approved_scope is None:
        return cli._run_apply(args)

    try:
        plan_path = cast(Path, args.plan).expanduser().resolve(strict=True)
        preflight_path = cast(Path, args.preflight).expanduser().resolve(strict=True)
        provenance_path = cast(Path, args.run_provenance).expanduser().resolve(strict=True)
        source_root, destination_root = validate_apply_roots(
            cast(Path, args.source_root).expanduser(),
            cast(Path, args.destination_root).expanduser(),
        )
        journal_arg = cast(Path | None, args.journal)
        journal_path = (
            journal_arg.expanduser().resolve(strict=False)
            if journal_arg is not None
            else None
        )
        if journal_path in {plan_path, preflight_path, provenance_path}:
            raise ApplyExecutionError("apply journal must be distinct from its inputs")

        prepared = prepare_apply(
            plan_path,
            preflight_path,
            provenance_path,
            approved_plan_sha256=cast(str, args.approve_plan_sha256).casefold(),
            approved_review_session_sha256=cast(
                str, args.approve_review_session_sha256
            ).casefold(),
            approved_source_revision=cast(str, args.approve_source_revision).casefold(),
            separate_roots=source_root != destination_root,
        )
        prepared = bind_optional_scope(args, prepared)
        assert prepared.apply_scope_sha256 is not None
        _current_revision_matches(prepared, operation="scoped apply")

        token = approval_token(prepared, source_root, destination_root)
        check_only = bool(args.check_only)
        if check_only:
            result = execute_apply(
                prepared,
                source_root,
                destination_root,
                journal_path=None,
                check_only=True,
                resume=False,
            )
        else:
            supplied = cast(str | None, args.confirm_apply)
            if supplied is None:
                if not sys.stdin.isatty():
                    raise ApplyExecutionError(
                        "non-interactive scoped apply requires --confirm-apply"
                    )
                print(
                    "Exact scoped apply approval required. This will atomically move "
                    f"{total_moving_members(prepared)} files in "
                    f"{len(prepared.contract.groups)} operation groups."
                )
                print(f"Source root:      {source_root}")
                print(f"Destination root: {destination_root}")
                print(f"Apply scope SHA:  {prepared.apply_scope_sha256}")
                print(f"Confirmation token:\n{token}")
                supplied = input("Type the exact confirmation token: ").strip()
            if supplied != token:
                raise ApplyExecutionError(
                    "apply confirmation does not match the exact plan, review, revision, "
                    "scope, and roots"
                )
            result = execute_apply(
                prepared,
                source_root,
                destination_root,
                journal_path=journal_path,
                check_only=False,
                resume=bool(args.resume),
            )
    except KeyboardInterrupt:
        print(
            "Apply interrupted; inspect the scoped journal and use --resume only after "
            "verifying the exact roots and scope.",
            file=sys.stderr,
        )
        return 130
    except (
        ApplyExecutionError,
        ApplyFilesystemError,
        ApplyScopeError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"Apply failed safely: {exc}", file=sys.stderr)
        return cli.APPLY_FAILED_EXIT

    payload = result.to_dict()
    payload["apply_scope_sha256"] = prepared.apply_scope_sha256
    if bool(args.json_output):
        if check_only:
            payload["confirmation_token"] = token
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif check_only:
        print(
            "Scoped apply check ready: "
            f"plan={result.plan_sha256} scope={prepared.apply_scope_sha256} "
            f"groups={result.groups_total} members={total_moving_members(prepared)}"
        )
        print(f"Confirmation token:\n{token}")
    else:
        print(
            "Scoped apply complete: "
            f"plan={result.plan_sha256} scope={prepared.apply_scope_sha256} "
            f"groups={result.groups_completed}/{result.groups_total} "
            f"moved={result.members_moved} recovered={result.members_recovered} "
            f"journal={result.journal_path}"
        )
    return 0
