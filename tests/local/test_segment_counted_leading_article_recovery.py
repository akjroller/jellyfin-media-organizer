from __future__ import annotations

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

SHOW = ProviderIdentity("fixture", "article-show")


def _episode(
    number: int, title: str, *, identity: str | None = None
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", identity or f"episode-{number}"),
        season=1,
        number=number,
        title=title,
    )


def _catalog(episodes: tuple[ProviderEpisode, ...]) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key="episodes:article-show",
        cache_snapshot_id="catalog:article-show:v1",
        show_identity=SHOW,
        episodes=episodes,
    )


class Provider:
    provider_name = "fixture"

    def __init__(self, catalog: ProviderEpisodeCatalog) -> None:
        self.catalog = catalog

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        raise AssertionError("show search is not used during episode assignment")

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW
        return self.catalog


def _show() -> CanonicalShow:
    return CanonicalShow(
        source_key="Fabricated Article Series",
        provider_identity=SHOW,
        title="Fabricated Article Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )


def _sources(
    last_title: str = "The Bright Morning AAC2 0",
) -> tuple[SourceEpisodeInput, ...]:
    titles = (
        "First Story AAC2 0",
        "Second Story AAC2 0",
        "Third Story AAC2 0",
        "Fourth Story AAC2 0",
        last_title,
    )
    return tuple(
        SourceEpisodeInput(
            f"fabricated-source-{index}.mkv",
            ParseResult(
                series_hint="Fabricated Article Series",
                season=1,
                episodes=(10 + index,),
                title_hint=title,
            ),
        )
        for index, title in enumerate(titles, start=1)
    )


def _base_episodes() -> tuple[ProviderEpisode, ...]:
    return (
        _episode(1, "First Story"),
        _episode(2, "Second Story"),
        _episode(3, "Third Story"),
        _episode(4, "Fourth Story"),
    )


def test_proven_group_recovers_optional_leading_the() -> None:
    provider = Provider(_catalog((*_base_episodes(), _episode(5, "Bright Morning"))))

    result = assign_episode_group_with_provider(_show(), _sources(), provider)
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    recovered = by_source["fabricated-source-5.mkv"]

    assert result.status is AssignmentStatus.MATCHED
    assert recovered.status is AssignmentStatus.MATCHED
    assert recovered.episodes[0].identity == ProviderIdentity("fixture", "episode-5")
    assert "segment-counted-title-remap:group-proven" in recovered.evidence.reasons
    assert (
        "segment-counted-title-remap:unique-near-title-proof"
        in recovered.evidence.reasons
    )
    assert "segment-counted-title-near-score:1.000" in recovered.evidence.reasons


def test_optional_leading_the_does_not_choose_between_repeated_provider_titles() -> (
    None
):
    provider = Provider(
        _catalog(
            (
                *_base_episodes(),
                _episode(5, "Bright Morning", identity="bright-a"),
                _episode(6, "Bright Morning", identity="bright-b"),
            )
        )
    )

    result = assign_episode_group_with_provider(_show(), _sources(), provider)
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    ambiguous = by_source["fabricated-source-5.mkv"]

    assert result.status is AssignmentStatus.UNRESOLVED
    assert ambiguous.status is AssignmentStatus.UNRESOLVED
    assert not ambiguous.episodes
    assert "segment-counted-title-remap:missing-exact-title-proof" in (
        ambiguous.evidence.reasons
    )


def test_unrelated_title_remains_blocked() -> None:
    provider = Provider(_catalog((*_base_episodes(), _episode(5, "Bright Morning"))))

    result = assign_episode_group_with_provider(
        _show(),
        _sources("Completely Different Story AAC2 0"),
        provider,
    )
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    unresolved = by_source["fabricated-source-5.mkv"]

    assert unresolved.status is AssignmentStatus.UNRESOLVED
    assert not unresolved.episodes
    assert "segment-counted-title-remap:missing-exact-title-proof" in (
        unresolved.evidence.reasons
    )
