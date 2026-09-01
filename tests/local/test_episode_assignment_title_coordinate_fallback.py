from __future__ import annotations

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from jellyfin_show_organizer.models import CanonicalShow, NumberingMode, ParseResult
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderIdentity,
    ProviderSearchSnapshot,
)

pytestmark = pytest.mark.local

_SHOW_ID = ProviderIdentity("fixture", "show-one")


class FixtureProvider:
    provider_name = "fixture"

    def __init__(self, episodes: tuple[ProviderEpisode, ...]) -> None:
        self.episodes = episodes

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        raise AssertionError(f"unexpected show search: {title}")

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        assert show_identity == _SHOW_ID
        return ProviderEpisodeCatalog(
            provider="fixture",
            request_key="episodes:show-one",
            cache_snapshot_id="catalog-v1",
            show_identity=_SHOW_ID,
            episodes=self.episodes,
        )


def _episode(value: str, season: int, number: int, title: str) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", value),
        season=season,
        number=number,
        title=title,
    )


def _show() -> CanonicalShow:
    return CanonicalShow(
        source_key="Synthetic Series",
        provider_identity=_SHOW_ID,
        title="Synthetic Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )


def test_missing_coordinate_can_use_one_exact_unique_episode_title() -> None:
    provider = FixtureProvider(
        (
            _episode("provider-target", 2024, 11, "A Unique Episode Title"),
            _episode("other", 2024, 12, "Another Episode"),
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput(
                "episode.mkv",
                ParseResult(
                    season=2,
                    episodes=(5,),
                    title_hint="A Unique Episode Title",
                ),
            ),
        ),
        provider,
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity.value == "provider-target"
    assert "catalog-coordinate-missing:S02E05" in assignment.evidence.reasons
    assert (
        "catalog-title-fallback:unique:a unique episode title"
        in assignment.evidence.reasons
    )


def test_ambiguous_exact_episode_title_remains_suspicious() -> None:
    provider = FixtureProvider(
        (
            _episode("target-a", 2024, 11, "Repeated Title"),
            _episode("target-b", 2024, 12, "Repeated Title"),
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput(
                "episode.mkv",
                ParseResult(
                    season=2,
                    episodes=(5,),
                    title_hint="Repeated Title",
                ),
            ),
        ),
        provider,
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.SUSPICIOUS
    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert "catalog-title-fallback:ambiguous:repeated title" in (
        assignment.evidence.reasons
    )


def test_missing_title_keeps_missing_coordinate_unresolved() -> None:
    provider = FixtureProvider((_episode("target", 2024, 11, "Unique Title"),))

    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput(
                "episode.mkv",
                ParseResult(season=2, episodes=(5,)),
            ),
        ),
        provider,
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.UNRESOLVED
    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert "missing-aired-catalog-entry:S02E05" in assignment.evidence.reasons


def test_multi_episode_source_does_not_use_single_title_fallback() -> None:
    provider = FixtureProvider((_episode("target", 2024, 11, "Unique Title"),))

    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput(
                "multi.mkv",
                ParseResult(
                    season=2,
                    episodes=(5, 6),
                    title_hint="Unique Title",
                ),
            ),
        ),
        provider,
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.UNRESOLVED
    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert "missing-aired-catalog-entry:S02E05" in assignment.evidence.reasons
