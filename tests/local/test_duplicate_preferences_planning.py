from pathlib import Path

import pytest

from jellyfin_show_organizer.models import (
    CanonicalShow,
    CompanionStatus,
    MatchEvidence,
    NumberingMode,
    ParseResult,
    PlanEpisode,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.overrides import (
    DuplicatePreferenceOverride,
    OverrideCatalog,
    load_overrides,
)
from jellyfin_show_organizer.planner import (
    PlanningConfigurationError,
    _apply_duplicate_decisions,
    _plan_companions,
)
from jellyfin_show_organizer.sidecars import (
    AdjacentDisposition,
    AdjacentFile,
    CompanionGroup,
    CompanionKind,
    SidecarDiscovery,
)

pytestmark = pytest.mark.local


def _record(source: str) -> PlanRecord:
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
        operation_group_id="op-" + source.casefold().replace("/", "-"),
        provider_episodes=(
            PlanEpisode(
                tvmaze_episode_id=90001,
                season=1,
                number=1,
                title="Pilot",
            ),
        ),
    )


def _sidecars(source_video: str) -> SidecarDiscovery:
    subtitle = AdjacentFile(
        relative_path=source_video.removesuffix(".mkv") + ".en.srt",
        extension=".srt",
        fingerprint=SourceFingerprint(size=5, mtime_ns=11),
        disposition=AdjacentDisposition.ASSOCIATED,
        reason="subtitle-associated",
    )
    return SidecarDiscovery(
        companions=(
            CompanionGroup(
                source_video=source_video,
                kind=CompanionKind.SUBTITLE,
                suffix=".en",
                files=(subtitle,),
            ),
        ),
        unresolved=(),
        ignored=(),
    )


def _catalog(*preferences: DuplicatePreferenceOverride) -> OverrideCatalog:
    return OverrideCatalog(
        schema_version=2,
        shows=(),
        duplicate_preferences=preferences,
    )


def test_single_explicit_source_preference_selects_winner_and_keeps_sidecar_group():
    first = "Example Series/release-a.mkv"
    second = "Example Series/release-b.mkv"
    records = [_record(first), _record(second)]
    discovery = _sidecars(first)
    overrides = _catalog(
        DuplicatePreferenceOverride(
            source=second,
            rank=100,
            reasons=("reviewed preferred source",),
        )
    )

    planned = _apply_duplicate_decisions(records, discovery, overrides)
    by_source = {record.source.relative_path: record for record in planned}

    assert by_source[second].status is TerminalStatus.MATCHED
    assert by_source[second].duplicate is not None
    assert by_source[second].duplicate.winner == second
    assert "reviewed preferred source" in by_source[second].duplicate.evidence
    assert by_source[first].status is TerminalStatus.DUPLICATE

    companions = _plan_companions(discovery, tuple(planned))
    assert len(companions) == 1
    assert companions[0].status is CompanionStatus.DUPLICATE
    assert companions[0].source_video == first


def test_equal_explicit_ranks_remain_suspicious():
    first = "Example Series/release-a.mkv"
    second = "Example Series/release-b.mkv"
    records = [_record(first), _record(second)]
    overrides = _catalog(
        DuplicatePreferenceOverride(source=first, rank=10),
        DuplicatePreferenceOverride(source=second, rank=10),
    )

    planned = _apply_duplicate_decisions(
        records,
        SidecarDiscovery(companions=(), unresolved=(), ignored=()),
        overrides,
    )

    assert {record.status for record in planned} == {TerminalStatus.SUSPICIOUS}
    assert all(record.duplicate is not None for record in planned)
    assert all(
        record.duplicate.winner is None for record in planned if record.duplicate
    )


def test_unknown_duplicate_preference_reference_fails_closed():
    records = [
        _record("Example Series/release-a.mkv"),
        _record("Example Series/release-b.mkv"),
    ]
    overrides = _catalog(
        DuplicatePreferenceOverride(
            source="Example Series/not-in-plan.mkv",
            rank=100,
        )
    )

    with pytest.raises(PlanningConfigurationError, match="unknown or non-movable"):
        _apply_duplicate_decisions(
            records,
            SidecarDiscovery(companions=(), unresolved=(), ignored=()),
            overrides,
        )


def test_preference_for_non_collision_source_fails_closed():
    source = "Example Series/only-source.mkv"
    overrides = _catalog(DuplicatePreferenceOverride(source=source, rank=100))

    with pytest.raises(
        PlanningConfigurationError, match="not part of a destination collision"
    ):
        _apply_duplicate_decisions(
            [_record(source)],
            SidecarDiscovery(companions=(), unresolved=(), ignored=()),
            overrides,
        )


def test_schema_two_duplicate_preferences_load_and_hash_deterministically(
    tmp_path: Path,
):
    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"
    first.write_text(
        """schema_version = 2

[[duplicate_preferences]]
source = "Example Series/release-b.mkv"
rank = 100
reasons = ["reviewed preferred source", "higher quality source"]

[[duplicate_preferences]]
source = "Example Series/release-a.mkv"
rank = 10
""",
        encoding="utf-8",
    )
    second.write_text(
        """schema_version = 2

[[duplicate_preferences]]
source = "Example Series/release-a.mkv"
rank = 10

[[duplicate_preferences]]
source = "Example Series/release-b.mkv"
rank = 100
reasons = ["higher quality source", "reviewed preferred source"]
""",
        encoding="utf-8",
    )

    first_catalog = load_overrides(first)
    second_catalog = load_overrides(second)

    assert first_catalog.snapshot_id == second_catalog.snapshot_id
    preferred = first_catalog.duplicate_preference_for("example series/RELEASE-B.mkv")
    assert preferred is not None
    assert preferred.rank == 100


def test_duplicate_preferences_require_schema_two_and_relative_safe_sources(
    tmp_path: Path,
):
    schema_one = tmp_path / "schema-one.toml"
    schema_one.write_text(
        """schema_version = 1
[[duplicate_preferences]]
source = "Example Series/release-a.mkv"
rank = 1
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema_version 2"):
        load_overrides(schema_one)

    for unsafe in (
        "../release-a.mkv",
        "/absolute/release-a.mkv",
        "C:/Media/release-a.mkv",
        "C:Media/release-a.mkv",
    ):
        with pytest.raises(ValueError, match="duplicate preference source"):
            DuplicatePreferenceOverride(source=unsafe, rank=1)
