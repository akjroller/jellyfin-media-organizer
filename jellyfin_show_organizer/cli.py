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
from .models import TerminalStatus
from .overrides import load_overrides
from .planner import PlanningConfig, PlanningConfigurationError, execute_plan
from .review import render_override_stub

CommandHandler = Callable[[argparse.Namespace], int]
PLAN_SUCCESS_EXIT = 0
PLAN_CONFIGURATION_EXIT = 2
PLAN_PROVIDER_EXIT = 4
PLAN_UNRESOLVED_EXIT = 10
PLAN_PREFLIGHT_BLOCKED_EXIT = 20


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
        outcome = execute_plan(config)
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
