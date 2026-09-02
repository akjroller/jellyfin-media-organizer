from __future__ import annotations

import csv
import io
from dataclasses import replace

import pytest

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
from jellyfin_show_organizer.reports import (
    DUPLICATE_CSV_HEADER,
    render_audit_bundle,
    render_duplicates_csv,
    stable_duplicate_ref,
)
from jellyfin_show_organizer.review import stable_review_ref

pytestmark = pytest.mark.local

DESTINATION = "example series (2024)/season 01/example series (2024) s01e01 - pilot.mkv"
FIRST = "Example Series/release-a.mkv"
SECOND = "Example Series/release-b.mkv"


def _base_record(source: str, operation_group_id: str) -> PlanRecord:
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
        destination="Example Series (2024)/Season 01/Example Series (2024) S01E01 - Pilot.mkv",
        operation_group_id=operation_group_id,
    )


def _selected_decision() -> DuplicateDecision:
    return DuplicateDecision(
        destination_key=DESTINATION,
        candidates=(FIRST, SECOND),
        winner=SECOND,
        losers=(FIRST,),
        confidence=0.8,
        evidence=(
            "fabricated release-quality evidence",
            "non-selected candidates are duplicate/non-moving only; no deletion is authorized",
        ),
    )


def _selected_plan(*, reverse: bool = False) -> OrganizerPlan:
    decision = _selected_decision()
    winner = replace(_base_record(SECOND, "op-b"), duplicate=decision)
    loser = replace(
        _base_record(FIRST, "op-a"),
        status=TerminalStatus.DUPLICATE,
        duplicate=decision,
        reason="non-destructive duplicate loser",
    )
    records = (winner, loser) if reverse else (loser, winner)
    return OrganizerPlan(schema_version=1, overrides_version=1, records=records)


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_duplicate_report_emits_one_reviewable_row_per_collision() -> None:
    decision = _selected_decision()
    data = render_duplicates_csv(_selected_plan())
    rows = _rows(data)

    assert data.startswith(b"\xef\xbb\xbf")
    assert tuple(rows[0]) == DUPLICATE_CSV_HEADER
    assert len(rows) == 1
    row = rows[0]
    assert row["duplicate_ref"] == stable_duplicate_ref(decision)
    assert row["destination_key"] == DESTINATION
    assert row["decision_state"] == "winner-selected"
    assert row["candidate_count"] == "2"
    assert row["candidates"] == f"{FIRST}|{SECOND}"
    assert row["candidate_review_refs"] == (
        f"{stable_review_ref(FIRST)}|{stable_review_ref(SECOND)}"
    )
    assert row["winner"] == SECOND
    assert row["winner_review_ref"] == stable_review_ref(SECOND)
    assert row["losers"] == FIRST
    assert row["loser_review_refs"] == stable_review_ref(FIRST)
    assert row["confidence"] == "0.8"
    assert "fabricated release-quality evidence" in row["evidence"]
    assert row["record_statuses"] == "duplicate|matched"
    assert row["record_sources"] == f"{FIRST}|{SECOND}"
    assert row["operation_group_ids"] == "op-a|op-b"


def test_no_winner_duplicate_group_is_explicitly_review_required() -> None:
    decision = DuplicateDecision(
        destination_key=DESTINATION,
        candidates=(FIRST, SECOND),
        winner=None,
        losers=(),
        confidence=0.5,
        evidence=("fabricated ambiguity",),
    )
    records = tuple(
        replace(
            _base_record(source, operation_group),
            status=TerminalStatus.SUSPICIOUS,
            duplicate=decision,
            reason="fabricated ambiguity",
        )
        for source, operation_group in ((FIRST, "op-a"), (SECOND, "op-b"))
    )

    rows = _rows(
        render_duplicates_csv(
            OrganizerPlan(schema_version=1, overrides_version=1, records=records)
        )
    )

    assert len(rows) == 1
    assert rows[0]["decision_state"] == "review-required"
    assert rows[0]["winner"] == ""
    assert rows[0]["winner_review_ref"] == ""
    assert rows[0]["losers"] == ""
    assert rows[0]["record_statuses"] == "suspicious"


def test_duplicate_report_is_independent_of_record_insertion_order() -> None:
    forward = render_duplicates_csv(_selected_plan())
    reverse = render_duplicates_csv(_selected_plan(reverse=True))

    assert forward == reverse
    assert render_audit_bundle(_selected_plan()).duplicates_csv == forward


def test_duplicate_review_ref_normalizes_case_slashes_and_candidate_order() -> None:
    first = _selected_decision()
    equivalent_first = FIRST.upper().replace("/", "\\")
    equivalent_second = SECOND.upper()
    equivalent = DuplicateDecision(
        destination_key=DESTINATION.upper().replace("/", "\\"),
        candidates=(equivalent_second, equivalent_first),
        winner=equivalent_second,
        losers=(equivalent_first,),
        confidence=0.8,
        evidence=("different explanatory text does not change group identity",),
    )

    assert stable_duplicate_ref(first) == stable_duplicate_ref(equivalent)


def test_conflicting_repeated_duplicate_decisions_fail_closed() -> None:
    first_decision = _selected_decision()
    conflicting = DuplicateDecision(
        destination_key=DESTINATION,
        candidates=(FIRST, SECOND),
        winner=FIRST,
        losers=(SECOND,),
        confidence=0.8,
        evidence=("fabricated conflicting decision",),
    )
    records = (
        replace(_base_record(FIRST, "op-a"), duplicate=first_decision),
        replace(_base_record(SECOND, "op-b"), duplicate=conflicting),
    )
    plan = OrganizerPlan(schema_version=1, overrides_version=1, records=records)

    with pytest.raises(ValueError, match="conflicting duplicate decisions"):
        render_duplicates_csv(plan)


def test_audit_bundle_publishes_duplicate_report_with_header_when_empty() -> None:
    plan = OrganizerPlan(schema_version=1, overrides_version=1, records=())
    bundle = render_audit_bundle(plan)
    files = dict(bundle.files())

    assert files["duplicates.csv"] == bundle.duplicates_csv
    assert bundle.duplicates_csv.startswith(b"\xef\xbb\xbf")
    header = bundle.duplicates_csv.decode("utf-8-sig").splitlines()[0]
    assert header == ",".join(DUPLICATE_CSV_HEADER)
