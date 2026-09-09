from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import (
    CanonicalShow,
    CompanionPlanRecord,
    CompanionStatus,
    DuplicateDecision,
    ExtraDecision,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.preflight import PreflightFinding, PreflightResult
from jellyfin_show_organizer.reports import (
    remaining_rows,
    render_audit_bundle,
    render_remaining_csv,
    render_summary,
    write_audit_bundle,
)
from jellyfin_show_organizer.schema import stable_plan_hash

pytestmark = pytest.mark.local


def _source(path: str, size: int) -> SourceFile:
    return SourceFile(
        relative_path=path,
        extension=".mkv",
        fingerprint=SourceFingerprint(size=size, mtime_ns=1000 + size),
    )


def _matched(path: str, size: int = 10) -> PlanRecord:
    return PlanRecord(
        source=_source(path, size),
        status=TerminalStatus.MATCHED,
        parse=ParseResult(series_hint="Example", season=1, episodes=(1,)),
        show=CanonicalShow(
            source_key="example",
            tvmaze_id=100,
            title="Example",
            year=2024,
            numbering_mode=NumberingMode.AIRED,
        ),
        evidence=MatchEvidence(method="synthetic", confidence=1.0),
        destination="Example (2024)/Season 01/Example (2024) S01E01.mkv",
        operation_group_id="op-example",
    )


def _duplicate(path: str, size: int = 20) -> PlanRecord:
    winner = "Example/winner.mkv"
    decision = DuplicateDecision(
        destination_key="example/season 01/example s01e01.mkv",
        candidates=(winner, path),
        winner=winner,
        losers=(path,),
        confidence=1.0,
        evidence=("synthetic reviewed duplicate",),
    )
    return PlanRecord(
        source=_source(path, size),
        status=TerminalStatus.DUPLICATE,
        duplicate=decision,
        destination="Example (2024)/Season 01/Example (2024) S01E01.mkv",
        operation_group_id="op-duplicate",
        reason="reviewed duplicate loser",
    )


def _held(path: str, size: int = 30) -> PlanRecord:
    return PlanRecord(
        source=_source(path, size),
        status=TerminalStatus.HELD,
        evidence=MatchEvidence(method="human-hold", confidence=1.0),
        reason="human review kept source held",
    )


def _extra(path: str, size: int = 40) -> PlanRecord:
    return PlanRecord(
        source=_source(path, size),
        status=TerminalStatus.EXTRA,
        extra=ExtraDecision(kind="featurette", rule="synthetic"),
        destination="Example (2024)/Extras/Featurette.mkv",
    )


def _plan() -> OrganizerPlan:
    matched = _matched("Example/matched.mkv")
    companions = (
        CompanionPlanRecord(
            relative_path="Example/matched.en.srt",
            extension=".srt",
            fingerprint=SourceFingerprint(size=5, mtime_ns=2005),
            status=CompanionStatus.ASSOCIATED,
            reason="associated subtitle",
            source_video=matched.source.relative_path,
            operation_group_id="op-example",
            destination="Example (2024)/Season 01/Example (2024) S01E01.en.srt",
            kind="subtitle",
        ),
        CompanionPlanRecord(
            relative_path="Example/duplicate.en.srt",
            extension=".srt",
            fingerprint=SourceFingerprint(size=6, mtime_ns=2006),
            status=CompanionStatus.DUPLICATE,
            reason="duplicate companion",
            source_video="Example/duplicate.mkv",
        ),
        CompanionPlanRecord(
            relative_path="Example/poster.jpg",
            extension=".jpg",
            fingerprint=None,
            status=CompanionStatus.IGNORED,
            reason="ignored artwork",
        ),
        CompanionPlanRecord(
            relative_path="Example/unknown.srt",
            extension=".srt",
            fingerprint=None,
            status=CompanionStatus.UNRESOLVED,
            reason="unresolved companion",
        ),
    )
    return OrganizerPlan(
        schema_version=3,
        overrides_version=4,
        records=(
            matched,
            _extra("Example/extra.mkv"),
            _duplicate("Example/duplicate.mkv"),
            _held("Example/held.mkv"),
            PlanRecord(
                source=_source("Example/suspicious.mkv", 50),
                status=TerminalStatus.SUSPICIOUS,
                reason="review required",
            ),
            PlanRecord(
                source=_source("Example/unresolved.mkv", 60),
                status=TerminalStatus.UNRESOLVED,
                reason="resolution required",
            ),
        ),
        companions=companions,
    )


def _ready(plan: OrganizerPlan) -> PreflightResult:
    return PreflightResult(plan_hash=stable_plan_hash(plan), findings=())


def _blocked(plan: OrganizerPlan) -> PreflightResult:
    return PreflightResult(
        plan_hash=stable_plan_hash(plan),
        findings=(PreflightFinding(code="synthetic-block"),),
    )


def test_remaining_rows_include_every_nonmoving_or_blocking_status_only():
    rows = remaining_rows(_plan())
    observed = {(row["kind"], row["status"]) for row in rows}

    assert observed == {
        ("video", "duplicate"),
        ("video", "held"),
        ("video", "suspicious"),
        ("video", "unresolved"),
        ("companion", "duplicate"),
        ("companion", "ignored"),
        ("companion", "unresolved"),
    }
    assert not observed & {
        ("video", "matched"),
        ("video", "extra"),
        ("companion", "associated"),
    }
    impacts = {(row["status"], row["readiness_impact"]) for row in rows}
    assert ("duplicate", "intentional") in impacts
    assert ("held", "intentional") in impacts
    assert ("ignored", "intentional") in impacts
    assert ("suspicious", "blocking") in impacts
    assert ("unresolved", "blocking") in impacts


def test_remaining_csv_is_bom_encoded_and_deterministic():
    plan = _plan()
    first = render_remaining_csv(plan)
    second = render_remaining_csv(
        OrganizerPlan(
            schema_version=plan.schema_version,
            overrides_version=plan.overrides_version,
            records=tuple(reversed(plan.records)),
            companions=tuple(reversed(plan.companions)),
        )
    )

    assert first.startswith(b"\xef\xbb\xbf")
    assert first == second
    rows = list(csv.DictReader(io.StringIO(first.decode("utf-8-sig"))))
    keys = [(row["kind"], row["source"].casefold()) for row in rows]
    assert keys == sorted(keys)


def test_summary_distinguishes_readiness_from_library_completion():
    plan = _plan()
    no_preflight = render_summary(plan).decode("utf-8")
    ready_but_blocking = render_summary(plan, _ready(plan)).decode("utf-8")
    blocked = render_summary(plan, _blocked(plan)).decode("utf-8")

    assert "readiness_state=not-evaluated" in no_preflight
    assert "apply_safe=false" in no_preflight
    assert "readiness_state=blocked" in ready_but_blocking
    assert "remaining_blocking=3" in ready_but_blocking
    assert "library_fully_organized=false" in ready_but_blocking
    assert "readiness_state=blocked" in blocked


def test_ready_plan_with_only_intentional_remaining_is_apply_ready():
    plan = OrganizerPlan(
        schema_version=3,
        overrides_version=4,
        records=(_held("Example/held.mkv"),),
        companions=(
            CompanionPlanRecord(
                relative_path="Example/poster.jpg",
                extension=".jpg",
                fingerprint=None,
                status=CompanionStatus.IGNORED,
                reason="ignored artwork",
            ),
        ),
    )

    summary = render_summary(plan, _ready(plan)).decode("utf-8")
    assert "readiness_state=apply-ready" in summary
    assert "apply_safe=true" in summary
    assert "library_fully_organized=false" in summary
    assert "remaining_total=2" in summary
    assert "remaining_intentional=2" in summary
    assert "remaining_blocking=0" in summary


def test_empty_ready_plan_is_fully_organized():
    plan = OrganizerPlan(schema_version=3, overrides_version=4, records=())
    summary = render_summary(plan, _ready(plan)).decode("utf-8")
    assert "readiness_state=apply-ready" in summary
    assert "apply_safe=true" in summary
    assert "library_fully_organized=true" in summary
    assert "remaining_total=0" in summary


def test_audit_bundle_and_writer_emit_remaining_csv(tmp_path: Path):
    plan = _plan()
    bundle = render_audit_bundle(plan, _blocked(plan))
    assert bundle.remaining_csv == render_remaining_csv(plan)
    assert ("remaining.csv", bundle.remaining_csv) in bundle.files()

    output = tmp_path / "audit"
    written = write_audit_bundle(output, plan, _blocked(plan))
    assert (output / "remaining.csv").read_bytes() == written.remaining_csv
