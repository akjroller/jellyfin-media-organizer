from __future__ import annotations

from jellyfin_show_organizer.decision_hash import stable_decision_hash
from jellyfin_show_organizer.models import (
    CacheSnapshot,
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanProvenance,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.schema import stable_plan_hash


def _plan(
    snapshot_id: str,
    *,
    evidence_reason: str = "synthetic evidence",
    destination: str = "Example Series/Season 01/Example Series S01E01.mkv",
) -> OrganizerPlan:
    record = PlanRecord(
        source=SourceFile(
            relative_path="Example Source/episode.mkv",
            extension=".mkv",
            fingerprint=SourceFingerprint(size=100, mtime_ns=123456789),
        ),
        status=TerminalStatus.MATCHED,
        parse=ParseResult(series_hint="Example Series", season=1, episodes=(1,)),
        show=CanonicalShow(
            source_key="Example Source",
            tvmaze_id=12345,
            title="Example Series",
            year=2024,
            numbering_mode=NumberingMode.AIRED,
        ),
        evidence=MatchEvidence(
            method="synthetic-provider-search",
            confidence=1.0,
            reasons=(evidence_reason,),
        ),
        destination=destination,
        operation_group_id="op-synthetic",
    )
    return OrganizerPlan(
        schema_version=1,
        overrides_version=1,
        records=(record,),
        provenance=PlanProvenance(
            tool_version="0.1.0",
            config_snapshot_id="a" * 64,
            overrides_snapshot_id="b" * 64,
            cache_snapshots=(
                CacheSnapshot(
                    provider="tvmaze",
                    kind="search",
                    request_key="search:example series",
                    snapshot_id=snapshot_id,
                    state="ok",
                ),
            ),
        ),
    )


def test_decision_hash_ignores_cache_snapshot_identity() -> None:
    cached = _plan("c" * 64)
    refreshed = _plan("d" * 64)

    assert stable_plan_hash(cached) != stable_plan_hash(refreshed)
    assert stable_decision_hash(cached) == stable_decision_hash(refreshed)


def test_decision_hash_ignores_explanatory_evidence() -> None:
    first = _plan("c" * 64, evidence_reason="cache snapshot c")
    second = _plan("d" * 64, evidence_reason="cache snapshot d")

    assert stable_plan_hash(first) != stable_plan_hash(second)
    assert stable_decision_hash(first) == stable_decision_hash(second)


def test_decision_hash_changes_when_operational_destination_changes() -> None:
    first = _plan("c" * 64)
    second = _plan(
        "c" * 64,
        destination="Example Series/Season 01/Example Series S01E01 - Revised.mkv",
    )

    assert stable_decision_hash(first) != stable_decision_hash(second)
