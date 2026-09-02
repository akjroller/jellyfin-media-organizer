from __future__ import annotations

import csv
import io
from dataclasses import replace

import pytest

from jellyfin_show_organizer.decision_hash import stable_decision_hash
from jellyfin_show_organizer.models import (
    CanonicalShow,
    DuplicateDecision,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.reports import render_audit_bundle, render_mapping_csv
from jellyfin_show_organizer.schema import canonical_manifest_bytes, stable_plan_hash

pytestmark = pytest.mark.local

DESTINATION = "example series (2024)/season 01/example series (2024) s01e01.mkv"
FIRST = "Example Series/release-a.mkv"
SECOND = "Example Series/release-b.mkv"


def _base_record(source: str) -> PlanRecord:
    return PlanRecord(
        source=SourceFile(
            relative_path=source,
            extension=".mkv",
            fingerprint=SourceFingerprint(size=100, mtime_ns=10),
        ),
        status=TerminalStatus.MATCHED,
        parse=ParseResult(series_hint="Example Series", season=1, episodes=(1,)),
        show=CanonicalShow(
            source_key="Example Series",
            tvmaze_id=45001,
            title="Example Series",
            year=2024,
            numbering_mode=NumberingMode.AIRED,
        ),
        evidence=MatchEvidence(method="synthetic", confidence=1.0),
        destination="Example Series (2024)/Season 01/Example Series (2024) S01E01.mkv",
    )


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def _review_decision() -> DuplicateDecision:
    return DuplicateDecision(
        destination_key=DESTINATION,
        candidates=(FIRST, SECOND),
        winner=None,
        losers=(),
        confidence=0.5,
        evidence=("fabricated duplicate ambiguity",),
    )


def test_winnerless_duplicate_records_are_marked_duplicate_review() -> None:
    decision = _review_decision()
    records = tuple(
        replace(
            _base_record(source),
            status=TerminalStatus.SUSPICIOUS,
            duplicate=decision,
            reason="fabricated duplicate ambiguity",
        )
        for source in (FIRST, SECOND)
    )
    plan = OrganizerPlan(schema_version=1, overrides_version=1, records=records)

    mapping_rows = _rows(render_mapping_csv(plan))
    unresolved_rows = _rows(render_audit_bundle(plan).unresolved_csv)

    assert [row["review_family"] for row in mapping_rows] == [
        "duplicate-review",
        "duplicate-review",
    ]
    assert [row["review_family"] for row in unresolved_rows] == [
        "duplicate-review",
        "duplicate-review",
    ]
    assert all(row["status"] == "suspicious" for row in unresolved_rows)


def test_duplicate_loser_is_not_reported_as_duplicate_review() -> None:
    decision = DuplicateDecision(
        destination_key=DESTINATION,
        candidates=(FIRST, SECOND),
        winner=SECOND,
        losers=(FIRST,),
        confidence=0.8,
        evidence=("fabricated unique winner",),
    )
    plan = OrganizerPlan(
        schema_version=1,
        overrides_version=1,
        records=(
            replace(
                _base_record(FIRST),
                status=TerminalStatus.DUPLICATE,
                duplicate=decision,
                reason="non-destructive duplicate loser",
            ),
            replace(_base_record(SECOND), duplicate=decision),
        ),
    )

    rows = _rows(render_mapping_csv(plan))

    assert {row["status"] for row in rows} == {"duplicate", "matched"}
    assert all(row["review_family"] == "" for row in rows)


def test_summary_separates_duplicate_review_from_other_suspicious_records() -> None:
    decision = _review_decision()
    duplicate_review = replace(
        _base_record(FIRST),
        status=TerminalStatus.SUSPICIOUS,
        duplicate=decision,
        reason="fabricated duplicate ambiguity",
    )
    ordinary = PlanRecord(
        source=SourceFile(
            relative_path="Other Series/Unknown.mkv",
            extension=".mkv",
            fingerprint=SourceFingerprint(size=50, mtime_ns=11),
        ),
        status=TerminalStatus.SUSPICIOUS,
        reason="fabricated identification ambiguity",
    )
    plan = OrganizerPlan(
        schema_version=1,
        overrides_version=1,
        records=(duplicate_review, ordinary),
    )

    summary = render_audit_bundle(plan).summary_txt.decode("utf-8")

    assert "suspicious=2" in summary
    assert "duplicate_review=1" in summary
    assert "suspicious_excluding_duplicate_review=1" in summary
    assert "Example Series" not in summary
    assert "Other Series" not in summary


def test_report_only_classification_does_not_change_manifest_or_hashes() -> None:
    decision = _review_decision()
    record = replace(
        _base_record(FIRST),
        status=TerminalStatus.SUSPICIOUS,
        duplicate=decision,
        reason="fabricated duplicate ambiguity",
    )
    plan = OrganizerPlan(schema_version=1, overrides_version=1, records=(record,))
    bundle = render_audit_bundle(plan)

    assert bundle.plan_json == canonical_manifest_bytes(plan) + b"\n"
    assert bundle.plan_sha256 == f"{stable_plan_hash(plan)}\n".encode("ascii")
    assert bundle.decision_sha256 == f"{stable_decision_hash(plan)}\n".encode("ascii")
