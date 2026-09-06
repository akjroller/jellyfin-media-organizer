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

SHOW = ProviderIdentity("fixture", "aka-show")


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
        request_key="episodes:aka-show",
        cache_snapshot_id="catalog:aka-show:v1",
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
        source_key="Fabricated AKA Series",
        provider_identity=SHOW,
        title="Fabricated AKA Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )


def _sources(last_title: str = "Hidden Broadcast") -> tuple[SourceEpisodeInput, ...]:
    titles = (
        "First Story",
        "Second Story",
        "Third Story",
        "Fourth Story",
        last_title,
    )
    return tuple(
        SourceEpisodeInput(
            f"fabricated-aka-source-{index}.mkv",
            ParseResult(
                series_hint="Fabricated AKA Series",
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


def test_proven_group_recovers_provider_declared_aka_alias() -> None:
    provider = Provider(
        _catalog(
            (
                *_base_episodes(),
                _episode(5, "Skyward Signal (AKA Hidden Broadcast)"),
            )
        )
    )

    result = assign_episode_group_with_provider(_show(), _sources(), provider)
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    recovered = by_source["fabricated-aka-source-5.mkv"]

    assert result.status is AssignmentStatus.MATCHED
    assert recovered.status is AssignmentStatus.MATCHED
    assert recovered.episodes[0].identity == ProviderIdentity("fixture", "episode-5")
    assert "segment-counted-title-remap:group-proven" in recovered.evidence.reasons
    assert (
        "segment-counted-title-remap:provider-declared-aka-proof:hidden broadcast"
        in recovered.evidence.reasons
    )
    assert "segment-counted-title-remap:unique-near-title-proof" not in (
        recovered.evidence.reasons
    )
    assert not any(
        reason.startswith("segment-counted-title-near-score:")
        for reason in recovered.evidence.reasons
    )


def test_duplicate_provider_aka_alias_stays_blocked() -> None:
    provider = Provider(
        _catalog(
            (
                *_base_episodes(),
                _episode(5, "Skyward Signal (AKA Hidden Broadcast)", identity="aka-a"),
                _episode(6, "Night Signal (AKA Hidden Broadcast)", identity="aka-b"),
            )
        )
    )

    result = assign_episode_group_with_provider(_show(), _sources(), provider)
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    unresolved = by_source["fabricated-aka-source-5.mkv"]

    assert result.status is AssignmentStatus.UNRESOLVED
    assert unresolved.status is AssignmentStatus.UNRESOLVED
    assert not unresolved.episodes
    assert "segment-counted-title-remap:missing-exact-title-proof" in (
        unresolved.evidence.reasons
    )


def test_arbitrary_parenthetical_text_is_not_treated_as_alias() -> None:
    provider = Provider(
        _catalog((*_base_episodes(), _episode(5, "Skyward Signal (Part One)")))
    )

    result = assign_episode_group_with_provider(
        _show(), _sources("Hidden Broadcast"), provider
    )
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    unresolved = by_source["fabricated-aka-source-5.mkv"]

    assert unresolved.status is AssignmentStatus.UNRESOLVED
    assert not unresolved.episodes
    assert "segment-counted-title-remap:missing-exact-title-proof" in (
        unresolved.evidence.reasons
    )


def test_unrelated_title_remains_blocked() -> None:
    provider = Provider(
        _catalog(
            (
                *_base_episodes(),
                _episode(5, "Skyward Signal (AKA Hidden Broadcast)"),
            )
        )
    )

    result = assign_episode_group_with_provider(
        _show(), _sources("Completely Different Story"), provider
    )
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    unresolved = by_source["fabricated-aka-source-5.mkv"]

    assert unresolved.status is AssignmentStatus.UNRESOLVED
    assert not unresolved.episodes
    assert "segment-counted-title-remap:missing-exact-title-proof" in (
        unresolved.evidence.reasons
    )
