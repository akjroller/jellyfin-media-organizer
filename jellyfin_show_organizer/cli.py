from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from . import __version__
from .apply_execution import (
    ApplyExecutionError,
    approval_token,
    execute_apply,
    prepare_apply,
    total_moving_members,
)
from .apply_validation import ApplyFilesystemError, validate_apply_roots
from .models import TerminalStatus
from .providers import TvmazeProviderAdapter
from .review import render_override_stub
from .review_contract import ReviewContractCatalog, load_review_contract
from .review_execution import (
    PlanningConfig,
    PlanningConfigurationError,
    execute_plan,
    http_json_getter,
)
from .review_session import ReviewItemState, manifest_override_snapshot
from .review_system import (
    ReviewConfigurationError,
    load_review_answers,
    run_review_system,
)
from .run_provenance import detect_source_revision
from .tvmaze_cache import TvmazeCatalogCache

CommandHandler = Callable[[argparse.Namespace], int]
PLAN_SUCCESS_EXIT = 0
PLAN_CONFIGURATION_EXIT = 2
PLAN_PROVIDER_EXIT = 4
PLAN_UNRESOLVED_EXIT = 10
PLAN_PREFLIGHT_BLOCKED_EXIT = 20
REVIEW_INCOMPLETE_EXIT = 12
APPLY_FAILED_EXIT = 30


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone organizer command-line parser."""
    parser = argparse.ArgumentParser(
        prog="organizer",
        description=(
            "Plan-first Jellyfin show organization tooling with an explicitly "
            "approved, journaled apply boundary."
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
        help="Generate and preflight one immutable plan without moving media.",
        description=(
            "Inventory one explicitly selected Shows root, resolve cached provider "
            "metadata show-by-show, and write a non-mutating audit bundle."
        ),
    )
    plan_parser.add_argument("shows_root", type=Path)
    plan_parser.add_argument("--config", type=Path)
    plan_parser.add_argument("--destination-root", type=Path)
    plan_parser.add_argument("--output-dir", type=Path)
    plan_parser.add_argument("--cache-dir", type=Path)
    plan_parser.add_argument("--overrides", type=Path)
    plan_parser.add_argument(
        "--review-session",
        type=Path,
        help=(
            "Required with schema-5 reviewed overrides. The ledger is verified "
            "against the active contract before planning."
        ),
    )
    provider_mode = plan_parser.add_mutually_exclusive_group()
    provider_mode.add_argument(
        "--offline",
        action="store_const",
        dest="provider_mode",
        const="offline",
    )
    provider_mode.add_argument(
        "--refresh",
        action="store_const",
        dest="provider_mode",
        const="refresh",
    )
    provider_mode.add_argument(
        "--online",
        action="store_const",
        dest="provider_mode",
        const="online",
    )
    plan_parser.add_argument("--max-path-length", type=int)
    plan_parser.add_argument("--max-component-length", type=int)
    plan_parser.add_argument("--json", action="store_true", dest="json_output")
    plan_parser.add_argument("--verbose", action="store_true")
    plan_parser.set_defaults(handler=_run_plan)

    review_parser = subparsers.add_parser(
        "review",
        help="Review duplicate and held decisions without touching media.",
        description=(
            "Maintain a resumable review-session ledger and compile answered review "
            "items into a new active planner override file. Media mutation, deletion, "
            "and quarantine execution are unavailable."
        ),
    )
    review_parser.add_argument("plan", type=Path)
    review_parser.add_argument("--overrides", type=Path, required=True)
    review_parser.add_argument("--output", type=Path, required=True)
    review_parser.add_argument("--session", type=Path, required=True)
    review_parser.add_argument("--cache-dir", type=Path, required=True)
    review_parser.add_argument("--resume", action="store_true")
    review_parser.add_argument(
        "--answers",
        type=Path,
        help=(
            "Use a schema-2 stable-review-ref answers file. Answers are bound to "
            "the starting session and each item's fingerprint identity."
        ),
    )
    review_parser.add_argument("--show", dest="show_filter")
    review_parser.add_argument(
        "--kind",
        choices=("duplicate", "held"),
        dest="kind_filter",
    )
    review_parser.add_argument("--ref", dest="ref_filter")
    review_parser.add_argument("--pending-only", action="store_true")
    review_parser.add_argument(
        "--approve-partial",
        action="store_true",
        help=(
            "Acknowledge a fully answered narrowed --show/--kind/--ref review scope "
            "for further non-mutating planning. This is review state only and never "
            "authorizes media movement; apply still requires separate exact full-plan "
            "approval."
        ),
    )
    review_parser.add_argument(
        "--batch-accept-recommended",
        action="store_true",
        help=(
            "For the selected duplicate subset only, offer one explicit confirmation "
            "to accept each displayed recommended winner while persisting one bound "
            "decision per group."
        ),
    )
    review_parser.add_argument(
        "--batch-keep-held",
        action="store_true",
        help=(
            "For selected source-review items only, list every source and offer one "
            "explicit confirmation to leave them held and untouched. This makes no "
            "episode inference and authorizes no media movement."
        ),
    )
    review_mode = review_parser.add_mutually_exclusive_group()
    review_mode.add_argument("--offline", action="store_true")
    review_mode.add_argument("--online", action="store_true")
    review_parser.set_defaults(handler=_run_review)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Apply one exactly approved reviewed plan with a durable journal.",
        description=(
            "Revalidate and atomically move only matched/extra operation groups from "
            "one exact reviewed plan. Duplicate, held, and ignored records never move."
        ),
    )
    apply_parser.add_argument("plan", type=Path)
    apply_parser.add_argument("--preflight", type=Path, required=True)
    apply_parser.add_argument("--run-provenance", type=Path, required=True)
    apply_parser.add_argument("--source-root", type=Path, required=True)
    apply_parser.add_argument("--destination-root", type=Path, required=True)
    apply_parser.add_argument("--journal", type=Path)
    apply_parser.add_argument("--approve-plan-sha256", required=True)
    apply_parser.add_argument("--approve-review-session-sha256", required=True)
    apply_parser.add_argument("--approve-source-revision", required=True)
    apply_parser.add_argument(
        "--confirm-apply",
        help=(
            "Exact confirmation token printed by --check-only. If omitted, an "
            "interactive terminal must type the displayed token."
        ),
    )
    apply_parser.add_argument(
        "--check-only",
        action="store_true",
        help="Revalidate the exact contract and print its confirmation token.",
    )
    apply_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume only from the exact existing append-only journal.",
    )
    apply_parser.add_argument("--json", action="store_true", dest="json_output")
    apply_parser.set_defaults(handler=_run_apply)

    overrides_parser = subparsers.add_parser(
        "overrides",
        help="Inspect explicitly selected local override files.",
        description=(
            "Validate local planning overrides or derive a review starter from one "
            "explicit plan manifest without reading or mutating media."
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

    stub_parser = overrides_subparsers.add_parser(
        "stub",
        help="Emit a local override starter for unresolved plan records.",
        description=(
            "Validate one plan.json manifest and write a TOML override starter to "
            "stdout. Observed provider IDs remain comments until deliberately edited."
        ),
    )
    stub_parser.add_argument("plan", type=Path)
    stub_parser.set_defaults(handler=_run_overrides_stub)

    return parser


def _config_path(value: object, *, base: Path, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PlanningConfigurationError(f"config {field} must be a path string")
    path = Path(value)
    return path if path.is_absolute() else base / path


def _planning_config(args: argparse.Namespace) -> PlanningConfig:
    config_path = cast(Path | None, args.config)
    raw_plan: dict[str, object] = {}
    config_base = Path.cwd()
    if config_path is not None:
        config_file = config_path.expanduser().resolve(strict=True)
        config_base = config_file.parent
        raw = tomllib.loads(config_file.read_text(encoding="utf-8"))
        if set(raw) != {"schema_version", "plan"} or raw["schema_version"] != 1:
            raise PlanningConfigurationError("unsupported planning config contract")
        plan_value = raw["plan"]
        if not isinstance(plan_value, dict):
            raise PlanningConfigurationError("config plan must be a table")
        raw_plan = cast(dict[str, object], plan_value)
        allowed = {
            "destination_root",
            "output_dir",
            "cache_dir",
            "overrides",
            "provider_mode",
            "max_path_length",
            "max_component_length",
        }
        if set(raw_plan) - allowed:
            raise PlanningConfigurationError("planning config has unknown fields")

    def selected_path(name: str, *, required: bool) -> Path | None:
        cli_value = cast(Path | None, getattr(args, name))
        if cli_value is not None:
            return cli_value
        raw_value = raw_plan.get(name)
        if raw_value is not None:
            return _config_path(raw_value, base=config_base, field=name)
        if required:
            raise PlanningConfigurationError(f"{name} is required")
        return None

    provider_mode = cast(str | None, args.provider_mode)
    if provider_mode is None:
        raw_mode = raw_plan.get("provider_mode", "online")
        if raw_mode not in {"online", "offline", "refresh"}:
            raise PlanningConfigurationError("config provider_mode is invalid")
        provider_mode = cast(str, raw_mode)

    def selected_int(name: str, default: int) -> int:
        cli_value = cast(int | None, getattr(args, name))
        value = cli_value if cli_value is not None else raw_plan.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise PlanningConfigurationError(f"config {name} must be an integer")
        return value

    destination_root = selected_path("destination_root", required=True)
    output_dir = selected_path("output_dir", required=True)
    cache_dir = selected_path("cache_dir", required=True)
    assert destination_root is not None
    assert output_dir is not None
    assert cache_dir is not None
    return PlanningConfig(
        shows_root=cast(Path, args.shows_root),
        destination_root=destination_root,
        output_dir=output_dir,
        cache_dir=cache_dir,
        overrides_path=selected_path("overrides", required=False),
        offline=provider_mode == "offline",
        refresh=provider_mode == "refresh",
        max_path_length=selected_int("max_path_length", 240),
        max_component_length=selected_int("max_component_length", 180),
    )


def _run_plan(args: argparse.Namespace) -> int:
    try:
        config = _planning_config(args)
        review_session = cast(Path | None, args.review_session)
        outcome = execute_plan(
            config,
            review_session_path=review_session,
        )
    except (PlanningConfigurationError, OSError, RuntimeError, ValueError) as exc:
        detail = f": {exc}" if bool(args.verbose) else ""
        print(f"Planning failed safely{detail}", file=sys.stderr)
        return PLAN_CONFIGURATION_EXIT

    counts = Counter(record.status for record in outcome.plan.records)
    unresolved = counts[TerminalStatus.UNRESOLVED] + counts[TerminalStatus.SUSPICIOUS]
    if outcome.provider_failure:
        exit_code = PLAN_PROVIDER_EXIT
    elif not outcome.preflight.ready:
        only_status_findings = all(
            finding.code.startswith("blocking-plan-status:")
            for finding in outcome.preflight.findings
        )
        exit_code = (
            PLAN_UNRESOLVED_EXIT
            if unresolved and only_status_findings
            else PLAN_PREFLIGHT_BLOCKED_EXIT
        )
    else:
        exit_code = PLAN_SUCCESS_EXIT

    summary = {
        "schema_version": 1,
        "plan_sha256": outcome.preflight.plan_hash,
        "records": len(outcome.plan.records),
        "companions": len(outcome.plan.companions),
        "statuses": {status.value: counts[status] for status in TerminalStatus},
        "preflight_ready": outcome.preflight.ready,
        "preflight_findings": len(outcome.preflight.findings),
        "provider_failure": outcome.provider_failure,
        "exit_code": exit_code,
    }
    if bool(args.json_output):
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    else:
        state = "ready" if outcome.preflight.ready else "blocked"
        print(
            f"Plan {state}: hash={outcome.preflight.plan_hash} "
            f"records={len(outcome.plan.records)} "
            f"findings={len(outcome.preflight.findings)}"
        )
        if bool(args.verbose):
            print(f"Audit bundle: {config.output_dir.resolve(strict=False)}")
    return exit_code


def _no_interactive_input(_prompt: str) -> str:
    raise ReviewConfigurationError(
        "non-interactive review attempted an unstructured prompt"
    )


def _run_review(args: argparse.Namespace) -> int:
    plan_path = cast(Path, args.plan)
    overrides_path = cast(Path, args.overrides)
    output_path = cast(Path, args.output)
    session_path = cast(Path, args.session)
    cache_dir = cast(Path, args.cache_dir)
    answers_path = cast(Path | None, args.answers)
    try:
        plan_file = plan_path.expanduser().resolve(strict=True)
        override_file = overrides_path.expanduser().resolve(strict=True)
        output_file = output_path.expanduser().resolve(strict=False)
        session_file = session_path.expanduser().resolve(strict=False)
        if output_file == override_file or session_file in {override_file, output_file}:
            raise ReviewConfigurationError("review inputs and outputs must be distinct")
        if output_file.exists():
            raise ReviewConfigurationError("active override output already exists")
        if bool(args.resume):
            if not session_file.is_file():
                raise ReviewConfigurationError(
                    "--resume requires an existing session file"
                )
        elif session_file.exists():
            raise ReviewConfigurationError("new review session output already exists")
        if not output_file.parent.is_dir() or not session_file.parent.is_dir():
            raise ReviewConfigurationError(
                "review output parent directory does not exist"
            )

        manifest = json.loads(plan_file.read_text(encoding="utf-8"))
        override_payload = override_file.read_bytes()
        base_catalog = load_review_contract(override_file)
        recorded_override_snapshot = manifest_override_snapshot(manifest)
        if recorded_override_snapshot != base_catalog.snapshot_id:
            raise ReviewConfigurationError(
                "plan provenance does not match the supplied base override snapshot"
            )

        answers = None
        input_fn: Callable[[str], str]
        if answers_path is not None:
            answers = load_review_answers(
                answers_path.expanduser().resolve(strict=True).read_bytes()
            )
            input_fn = _no_interactive_input
        else:
            if not sys.stdin.isatty():
                raise ReviewConfigurationError(
                    "interactive review requires a TTY unless --answers is supplied"
                )
            input_fn = input

        if bool(args.approve_partial) and not any(
            (
                args.show_filter is not None,
                args.kind_filter is not None,
                args.ref_filter is not None,
            )
        ):
            raise ReviewConfigurationError(
                "--approve-partial requires an explicit --show, --kind, or --ref scope"
            )

        cache = TvmazeCatalogCache(
            cache_dir.expanduser().resolve(strict=False),
            offline=bool(args.offline),
            refresh=False,
        )
        provider = TvmazeProviderAdapter(cache, http_json_getter)
        session, _active = run_review_system(
            manifest,
            override_payload,
            base_override_snapshot=base_catalog.snapshot_id,
            provider=provider,
            session_path=session_file,
            output_override_path=output_file,
            resume=bool(args.resume),
            input_fn=input_fn,
            output=sys.stdout,
            show_filter=cast(str | None, args.show_filter),
            kind_filter=cast(str | None, args.kind_filter),
            ref_filter=cast(str | None, args.ref_filter),
            pending_only=bool(args.pending_only),
            batch_accept_recommended=bool(args.batch_accept_recommended),
            batch_keep_held=bool(args.batch_keep_held),
            approve_partial=bool(args.approve_partial),
            answers=answers,
        )
        active_catalog = load_review_contract(output_file)
        if not isinstance(active_catalog, ReviewContractCatalog):
            raise ReviewConfigurationError("review did not produce a schema-5 contract")
        if active_catalog.review_session_sha256 != session.sha256:
            raise ReviewConfigurationError(
                "active override is not bound to the resulting review session"
            )
    except KeyboardInterrupt:
        print(
            "Review interrupted; the last atomically saved session remains resumable.",
            file=sys.stderr,
        )
        return 130
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ReviewConfigurationError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"Review failed safely: {exc}", file=sys.stderr)
        return 2

    states = Counter(item.state for item in session.items)
    state_text = (
        f"session_sha256={session.sha256} "
        f"answered={states[ReviewItemState.ANSWERED]} "
        f"deferred={states[ReviewItemState.DEFERRED]} "
        f"pending={states[ReviewItemState.PENDING]} "
        f"override_snapshot={active_catalog.snapshot_id}"
    )
    if session.complete:
        print(f"Review complete: {state_text}")
        return 0
    if session.approved_partial:
        print(
            "Review saved: approved partial review-state only; no movement authorized. "
            f"scope_items={len(session.approved_scope_refs)} {state_text}"
        )
        return 0
    print(f"Review saved: partial {state_text}")
    return REVIEW_INCOMPLETE_EXIT


def _run_apply(args: argparse.Namespace) -> int:
    try:
        plan_path = cast(Path, args.plan).expanduser().resolve(strict=True)
        preflight_path = cast(Path, args.preflight).expanduser().resolve(strict=True)
        provenance_path = (
            cast(Path, args.run_provenance).expanduser().resolve(strict=True)
        )
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
        current_revision = detect_source_revision()
        if current_revision.state != "git":
            raise ApplyExecutionError(
                "apply requires a verifiable clean Git source revision"
            )
        if current_revision.dirty:
            raise ApplyExecutionError(
                "apply refuses to run from a dirty source checkout"
            )
        if current_revision.commit != prepared.source_revision:
            raise ApplyExecutionError(
                "running source revision does not match the approved plan"
            )

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
                        "non-interactive apply requires --confirm-apply"
                    )
                print(
                    "Exact apply approval required. This will atomically move "
                    f"{total_moving_members(prepared)} files in "
                    f"{len(prepared.contract.groups)} operation groups."
                )
                print(f"Source root:      {source_root}")
                print(f"Destination root: {destination_root}")
                print(f"Confirmation token:\n{token}")
                supplied = input("Type the exact confirmation token: ").strip()
            if supplied != token:
                raise ApplyExecutionError(
                    "apply confirmation does not match the exact plan, review, "
                    "revision, and roots"
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
            "Apply interrupted; inspect the journal and use --resume only after "
            "verifying the exact roots.",
            file=sys.stderr,
        )
        return 130
    except (
        ApplyExecutionError,
        ApplyFilesystemError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"Apply failed safely: {exc}", file=sys.stderr)
        return APPLY_FAILED_EXIT

    payload = result.to_dict()
    if bool(args.json_output):
        if check_only:
            payload["confirmation_token"] = token
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    elif check_only:
        print(
            "Apply check ready: "
            f"plan={result.plan_sha256} groups={result.groups_total} "
            f"members={total_moving_members(prepared)}"
        )
        print(f"Confirmation token:\n{token}")
    else:
        print(
            "Apply complete: "
            f"plan={result.plan_sha256} groups={result.groups_completed}/"
            f"{result.groups_total} moved={result.members_moved} "
            f"recovered={result.members_recovered} journal={result.journal_path}"
        )
    return 0


def _run_overrides_validate(args: argparse.Namespace) -> int:
    path = cast(Path, args.path)
    try:
        catalog = load_review_contract(path)
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


def _run_overrides_stub(args: argparse.Namespace) -> int:
    path = cast(Path, args.plan)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        rendered = render_override_stub(manifest).decode("utf-8")
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        print(f"Plan manifest invalid: cannot read file ({detail})", file=sys.stderr)
        return 2
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Plan manifest invalid: {exc}", file=sys.stderr)
        return 2

    print(rendered, end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone organizer CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = cast(CommandHandler, args.handler)
    return handler(args)
