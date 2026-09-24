"""Supported integration API for applications embedding JMO.

This module intentionally exposes only immutable planning and audit inspection.
Filesystem mutation remains available only through the explicit CLI apply
contract, not through the convenience API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .planner import PlanningConfig, PlanningOutcome
from .review_execution import execute_plan
from .summary_io import read_summary, summary_int
from .tvmaze_cache import Clock, JsonGetter


@dataclass(frozen=True, slots=True)
class AuditSummary:
    """Path-independent counts and readiness extracted from summary.txt."""

    run_dir: Path
    readiness_state: str
    preflight_ready: str
    records: int
    matched: int
    extra: int
    duplicate: int
    held: int
    suspicious: int
    unresolved: int
    remaining_total: int
    plan_sha256: str | None


def plan_library(
    config: PlanningConfig,
    getter: JsonGetter,
    *,
    clock: Clock | None = None,
    review_session_path: Path | None = None,
    progress=None,
) -> PlanningOutcome:
    """Build one immutable, non-mutating plan for an embedding application."""

    return execute_plan(
        config,
        getter,
        clock=clock,
        review_session_path=review_session_path,
        progress=progress,
    )


def inspect_audit(run_dir: Path) -> AuditSummary:
    """Read one completed audit bundle without touching its media root."""

    root = run_dir.expanduser().resolve(strict=True)
    summary = root / "summary.txt"
    if not summary.is_file():
        raise FileNotFoundError(f"audit bundle does not contain summary.txt: {root}")
    values = read_summary(summary)

    return AuditSummary(
        run_dir=root,
        readiness_state=values.get("readiness_state", "not-evaluated"),
        preflight_ready=values.get("preflight_ready", "unknown"),
        records=summary_int(values, "records"),
        matched=summary_int(values, "matched"),
        extra=summary_int(values, "extra"),
        duplicate=summary_int(values, "duplicate"),
        held=summary_int(values, "held"),
        suspicious=summary_int(values, "suspicious"),
        unresolved=summary_int(values, "unresolved"),
        remaining_total=summary_int(values, "remaining_total"),
        plan_sha256=values.get("plan_sha256"),
    )
