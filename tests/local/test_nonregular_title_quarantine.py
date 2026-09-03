from __future__ import annotations

from collections.abc import Mapping

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    NumberingMode,
    ParseResult,
    ProviderIdentity,
)
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
)

pytestmark = pytest.mark.local

SHOW = ProviderIdentity("fixture", "show")


def _episode(
    identity: str,
    *,
    season: int,
    number: int | None,
    title: str,
    episode_type: str | None = "regular",
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", identity),
        season=season,
        number=number,
        title=title,
        episode_type=episode_type,
    )


def _catalog(
    episodes: tuple[ProviderEpisode, ...],
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key="episodes:show",
        cache_snapshot_id="catalog:v1",
        show_identity=SHOW,
        episodes=episodes,
    )


class Provider:
    provider_name = "fixture"

    def __init__(self, catalog: ProviderEpisodeCatalog) -> None:
        self.catalog = catalog
        self.catalog_calls = 0

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        raise AssertionError("show search is not expected during assignment")

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW
        self.catalog_calls += 1
        return self.catalog


def _show() -> CanonicalShow:
    return CanonicalShow(
        source_key="Example Series",
        provider_identity=SHOW,
        title="Example Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )


def _source(key: str, episode: int, title: str) -> SourceEpisodeInput:
    return SourceEpisodeInput(
        key,
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(episode,),
            title_hint=title,
        ),
    )


def test_unnumbered_nonregular_title_is_quarantined_from_regular_coordinate() -> None:
    provider = Provider(
        _catalog(
            (
                _episode("regular-1", season=1, number=1, title="Regular Story"),
                _episode(
                    "movie-event",
                    season=1,
                    number=None,
                    title="Movie Event",
                    episode_type="significant_special",
                ),
            )
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            _source("regular.mkv", 1, "Regular Story"),
            _source("movie.mkv", 1, "Movie Event"),
        ),
        provider,
    )
    by_source = {assignment.source_key: assignment for assignment in result.assignments}

    assert by_source["regular.mkv"].status is AssignmentStatus.MATCHED
    assert [episode.identity.value for episode in by_source["regular.mkv"].episodes] == [
        "regular-1"
    ]

    movie = by_source["movie.mkv"]
    assert movie.status is AssignmentStatus.UNRESOLVED
    assert not movie.episodes
    assert "nonregular-title-quarantine:unique-exact-title" in movie.evidence.reasons
    assert (
        "nonregular-title-quarantine:provider-entry-missing-number"
        in movie.evidence.reasons
    )
    assert provider.catalog_calls >= 2


def test_ambiguous_nonregular_title_does_not_quarantine() -> None:
    provider = Provider(
        _catalog(
            (
                _episode("regular-1", season=1, number=1, title="Regular Story"),
                _episode(
                    "special-a",
                    season=1,
                    number=None,
                    title="Movie Event",
                    episode_type="significant_special",
                ),
                _episode(
                    "special-b",
                    season=0,
                    number=None,
                    title="Movie Event",
                    episode_type="significant_special",
                ),
            )
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            _source("regular.mkv", 1, "Regular Story"),
            _source("movie.mkv", 1, "Movie Event"),
        ),
        provider,
    )

    assert all(
        assignment.status is AssignmentStatus.SUSPICIOUS
        for assignment in result.assignments
    )
    assert not any(
        reason.startswith("nonregular-title-quarantine:")
        for assignment in result.assignments
        for reason in assignment.evidence.reasons
    )


def test_numbered_nonregular_title_does_not_quarantine() -> None:
    provider = Provider(
        _catalog(
            (
                _episode("regular-1", season=1, number=1, title="Regular Story"),
                _episode(
                    "special-9",
                    season=0,
                    number=9,
                    title="Movie Event",
                    episode_type="significant_special",
                ),
            )
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            _source("regular.mkv", 1, "Regular Story"),
            _source("movie.mkv", 1, "Movie Event"),
        ),
        provider,
    )

    assert all(
        assignment.status is AssignmentStatus.SUSPICIOUS
        for assignment in result.assignments
    )


def test_ordinary_nonduplicate_group_does_not_trigger_extra_catalog_fetch() -> None:
    provider = Provider(
        _catalog(
            (
                _episode("regular-1", season=1, number=1, title="Regular Story"),
                _episode("regular-2", season=1, number=2, title="Second Story"),
            )
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            _source("one.mkv", 1, "Regular Story"),
            _source("two.mkv", 2, "Second Story"),
        ),
        provider,
    )

    assert result.status is AssignmentStatus.MATCHED
    assert provider.catalog_calls == 1
