from __future__ import annotations

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from jellyfin_show_organizer.models import CanonicalShow, NumberingMode, ParseResult, ProviderIdentity
from jellyfin_show_organizer.providers import ProviderEpisode, ProviderEpisodeCatalog

pytestmark = pytest.mark.local

SHOW_ID = ProviderIdentity("fixture", "show")


def _show(mode: NumberingMode = NumberingMode.AIRED) -> CanonicalShow:
    return CanonicalShow(
        source_key="Example Collection",
        provider_identity=SHOW_ID,
        title="Example Collection",
        year=2024,
        numbering_mode=mode,
    )


def _episode(value: str, season: int, number: int, title: str) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", value),
        season=season,
        number=number,
        title=title,
    )


class Provider:
    provider_name = "fixture"

    def __init__(self) -> None:
        self.catalog_calls = 0

    def search_shows(self, title: str):
        raise AssertionError(title)

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW_ID
        self.catalog_calls += 1
        return ProviderEpisodeCatalog(
            provider="fixture",
            request_key="episodes:show",
            cache_snapshot_id="catalog:v1",
            show_identity=SHOW_ID,
            episodes=(
                _episode("special", 0, 1, "Preview"),
                _episode("one", 1, 1, "Arrival"),
                _episode("two", 1, 2, "Departure"),
                _episode("three", 1, 3, "Part Beta"),
            ),
        )


def test_mixed_aired_absolute_and_segment_families_assign_independently() -> None:
    provider = Provider()
    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput("aired.mkv", ParseResult(season=1, episodes=(1,))),
            SourceEpisodeInput("absolute.mkv", ParseResult(absolute_episode=2)),
            SourceEpisodeInput(
                "segment.mkv",
                ParseResult(segment_hint="b", title_hint="Part Beta"),
            ),
        ),
        provider,
    )

    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    assert result.status is AssignmentStatus.MATCHED
    assert by_source["aired.mkv"].episodes[0].identity == ProviderIdentity("fixture", "one")
    assert by_source["absolute.mkv"].episodes[0].identity == ProviderIdentity("fixture", "two")
    assert by_source["segment.mkv"].episodes[0].identity == ProviderIdentity("fixture", "three")
    assert "mixed-numbering-family:absolute" in by_source["absolute.mkv"].evidence.reasons
    assert "mixed-numbering-family:segment" in by_source["segment.mkv"].evidence.reasons
    assert provider.catalog_calls == 3


def test_bad_alternate_family_does_not_poison_primary_aired_source() -> None:
    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput("aired.mkv", ParseResult(season=1, episodes=(1,))),
            SourceEpisodeInput("segment.mkv", ParseResult(segment_hint="b")),
        ),
        Provider(),
    )

    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    assert result.status is AssignmentStatus.UNRESOLVED
    assert by_source["aired.mkv"].status is AssignmentStatus.MATCHED
    assert by_source["aired.mkv"].episodes[0].identity == ProviderIdentity("fixture", "one")
    assert by_source["segment.mkv"].status is AssignmentStatus.UNRESOLVED
    assert "missing-segment-title-evidence" in by_source["segment.mkv"].evidence.reasons


def test_cross_family_provider_episode_collision_stays_suspicious() -> None:
    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput("aired.mkv", ParseResult(season=1, episodes=(2,))),
            SourceEpisodeInput("absolute.mkv", ParseResult(absolute_episode=2)),
        ),
        Provider(),
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert all(assignment.status is AssignmentStatus.SUSPICIOUS for assignment in result.assignments)
    assert all(not assignment.episodes for assignment in result.assignments)
    assert all(
        any(reason.startswith("duplicate-provider-episode-assignment:") for reason in assignment.evidence.reasons)
        for assignment in result.assignments
    )


def test_selected_policy_is_not_bypassed_when_expected_family_is_absent() -> None:
    provider = Provider()
    result = assign_episode_group_with_provider(
        _show(NumberingMode.ABSOLUTE),
        (SourceEpisodeInput("aired.mkv", ParseResult(season=1, episodes=(1,))),),
        provider,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert provider.catalog_calls == 0
    assert "numbering-policy-conflict:expected-absolute:observed-aired" in result.assignments[0].evidence.reasons
