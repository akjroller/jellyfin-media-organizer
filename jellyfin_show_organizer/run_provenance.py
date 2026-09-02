from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .decision_hash import stable_decision_hash
from .models import CompanionStatus, OrganizerPlan, TerminalStatus
from .schema import PLAN_SCHEMA_VERSION, stable_plan_hash

_GIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
_RUN_PROVENANCE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SourceRevision:
    state: str
    commit: str | None
    dirty: bool | None

    def __post_init__(self) -> None:
        if self.state not in {"git", "unavailable"}:
            raise ValueError("source revision state is invalid")
        if self.state == "git":
            if self.commit is None or _GIT_SHA_RE.fullmatch(self.commit) is None:
                raise ValueError("git source revision requires a 40-character commit")
            if self.dirty is None:
                raise ValueError("git source revision requires dirty state")
        elif self.commit is not None or self.dirty is not None:
            raise ValueError("unavailable source revision cannot carry git metadata")


@dataclass(frozen=True, slots=True)
class RunProvenance:
    tool_version: str
    source_revision: SourceRevision
    plan_sha256: str
    decision_sha256: str
    config_snapshot_id: str
    overrides_snapshot_id: str
    provider_name: str
    provider_mode: str
    provider_failure: bool
    cache_snapshot_count: int
    cache_states: tuple[tuple[str, int], ...]
    cache_kinds: tuple[tuple[str, int], ...]
    max_path_length: int
    max_component_length: int
    overrides_configured: bool
    record_count: int
    record_statuses: tuple[tuple[str, int], ...]
    companion_count: int
    companion_statuses: tuple[tuple[str, int], ...]
    preflight_ready: bool
    preflight_finding_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _RUN_PROVENANCE_SCHEMA_VERSION,
            "tool_version": self.tool_version,
            "plan_schema_version": PLAN_SCHEMA_VERSION,
            "source_revision": {
                "state": self.source_revision.state,
                "commit": self.source_revision.commit,
                "dirty": self.source_revision.dirty,
            },
            "plan_sha256": self.plan_sha256,
            "decision_sha256": self.decision_sha256,
            "config_snapshot_id": self.config_snapshot_id,
            "overrides_snapshot_id": self.overrides_snapshot_id,
            "provider": {
                "name": self.provider_name,
                "mode": self.provider_mode,
                "failure": self.provider_failure,
                "cache_snapshot_count": self.cache_snapshot_count,
                "cache_states": dict(self.cache_states),
                "cache_kinds": dict(self.cache_kinds),
            },
            "planning": {
                "max_path_length": self.max_path_length,
                "max_component_length": self.max_component_length,
                "overrides_configured": self.overrides_configured,
            },
            "records": {
                "total": self.record_count,
                "statuses": dict(self.record_statuses),
            },
            "companions": {
                "total": self.companion_count,
                "statuses": dict(self.companion_statuses),
            },
            "preflight": {
                "ready": self.preflight_ready,
                "finding_count": self.preflight_finding_count,
            },
        }


def _run_git(root: Path, *args: str) -> tuple[int, str] | None:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.returncode, completed.stdout


def detect_source_revision(source_root: Path | None = None) -> SourceRevision:
    """Best-effort source revision detection that never exposes checkout paths."""

    root = source_root or Path(__file__).resolve().parent.parent
    inside = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside[0] != 0 or inside[1].strip() != "true":
        return SourceRevision(state="unavailable", commit=None, dirty=None)

    head = _run_git(root, "rev-parse", "HEAD")
    if head is None or head[0] != 0:
        return SourceRevision(state="unavailable", commit=None, dirty=None)
    commit = head[1].strip()
    if _GIT_SHA_RE.fullmatch(commit) is None:
        return SourceRevision(state="unavailable", commit=None, dirty=None)

    status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status is None or status[0] != 0:
        return SourceRevision(state="unavailable", commit=None, dirty=None)
    return SourceRevision(state="git", commit=commit.casefold(), dirty=bool(status[1]))


def _status_counts(plan: OrganizerPlan) -> tuple[tuple[str, int], ...]:
    counts = Counter(record.status for record in plan.records)
    return tuple((status.value, counts[status]) for status in TerminalStatus)


def _companion_status_counts(plan: OrganizerPlan) -> tuple[tuple[str, int], ...]:
    counts = Counter(record.status for record in plan.companions)
    return tuple((status.value, counts[status]) for status in CompanionStatus)


def build_run_provenance(
    plan: OrganizerPlan,
    *,
    source_revision: SourceRevision,
    provider_mode: str,
    provider_failure: bool,
    max_path_length: int,
    max_component_length: int,
    overrides_configured: bool,
    preflight_ready: bool,
    preflight_finding_count: int,
) -> RunProvenance:
    provenance = plan.provenance
    if provenance is None:
        raise ValueError("run provenance requires plan provenance")
    if provider_mode not in {"online", "offline", "refresh"}:
        raise ValueError("run provenance provider mode is invalid")

    cache_state_counts = Counter(item.state for item in provenance.cache_snapshots)
    cache_kind_counts = Counter(item.kind for item in provenance.cache_snapshots)
    return RunProvenance(
        tool_version=provenance.tool_version,
        source_revision=source_revision,
        plan_sha256=stable_plan_hash(plan),
        decision_sha256=stable_decision_hash(plan),
        config_snapshot_id=provenance.config_snapshot_id,
        overrides_snapshot_id=provenance.overrides_snapshot_id,
        provider_name="tvmaze",
        provider_mode=provider_mode,
        provider_failure=provider_failure,
        cache_snapshot_count=len(provenance.cache_snapshots),
        cache_states=tuple(sorted(cache_state_counts.items())),
        cache_kinds=tuple(sorted(cache_kind_counts.items())),
        max_path_length=max_path_length,
        max_component_length=max_component_length,
        overrides_configured=overrides_configured,
        record_count=len(plan.records),
        record_statuses=_status_counts(plan),
        companion_count=len(plan.companions),
        companion_statuses=_companion_status_counts(plan),
        preflight_ready=preflight_ready,
        preflight_finding_count=preflight_finding_count,
    )


def render_run_provenance(provenance: RunProvenance) -> bytes:
    return (
        json.dumps(
            provenance.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
