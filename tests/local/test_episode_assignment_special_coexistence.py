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
        self.catalog_calls = 0

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        raise AssertionError(f"unexpected show search: {title}")

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        assert show_identity == _SHOW_ID
        self.catalog_calls += 1
        return ProviderEpisodeCatalog(
            provider="fixture",
            request_key="episodes:show-one",
            cache_snapshot_id="catalog-v1",
            show_identity=_SHOW_ID,
            episodes=self.episodes,
        )


def _episode(
    value: str,
    *,
    season: int,
    number: int,
    title: str,
    episode_type: str = "regular",
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", value),
        season=season,
        number=number,
        title=title,
        episode_type=episode_type,
    )


def _show(mode: NumberingMode = NumberingMode.AIRED) -> CanonicalShow:
    return CanonicalShow(
        source_key="Synthetic Series",
        provider_identity=_SHOW_ID,
        title="Synthetic Series",
        year=2024,
        numbering_mode=mode,
    )


def test_special_can_coexist_with_primary_aired_numbering() -> None:
    provider = FixtureProvider(
        (
            _episode("regular-1", season=1, number=1, title="Pilot"),
            _episode(
                "special-1",
                season=0,
                number=1,
                title="OAD 1",
                episode_type="special",
            ),
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput(
                "regular.mkv",
                ParseResult(season=1, episodes=(1,)),
            ),
            SourceEpisodeInput(
                "oad.mkv",
                ParseResult(special_kind="oad", special_episode=1),
            ),
        ),
        provider,
    )

    assert result.status is AssignmentStatus.MATCHED
    by_source = {item.source_key: item for item in result.assignments}
    assert by_source["regular.mkv"].status is AssignmentStatus.MATCHED
    assert by_source["regular.mkv"].episodes[0].identity.value == "regular-1"
    assert by_source["oad.mkv"].status is AssignmentStatus.MATCHED
    assert by_source["oad.mkv"].episodes[0].identity.value == "special-1"
    assert "accessory-special-under:aired" in by_source["oad.mkv"].evidence.reasons
    assert provider.catalog_calls == 1


def test_ambiguous_special_does_not_poison_primary_episode_assignment() -> None:
    provider = FixtureProvider(
        (
            _episode("regular-1", season=1, number=1, title="Pilot"),
            _episode(
                "special-a",
                season=0,
                number=1,
                title="Bonus A",
                episode_type="special",
            ),
            _episode(
                "special-b",
                season=0,
                number=1,
                title="Bonus B",
                episode_type="special",
            ),
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput(
                "regular.mkv",
                ParseResult(season=1, episodes=(1,)),
            ),
            SourceEpisodeInput(
                "oad.mkv",
                ParseResult(special_kind="oad", special_episode=1),
            ),
        ),
        provider,
    )

    by_source = {item.source_key: item for item in result.assignments}
    assert result.status is AssignmentStatus.SUSPICIOUS
    assert by_source["regular.mkv"].status is AssignmentStatus.MATCHED
    assert by_source["oad.mkv"].status is AssignmentStatus.SUSPICIOUS
    assert "ambiguous-special-catalog-entry:1" in by_source["oad.mkv"].evidence.reasons


def test_special_only_group_does_not_override_primary_numbering_policy() -> None:
    provider = FixtureProvider(
        (
            _episode(
                "special-1",
                season=0,
                number=1,
                title="OAD 1",
                episode_type="special",
            ),
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput(
                "oad.mkv",
                ParseResult(special_kind="oad", special_episode=1),
            ),
        ),
        provider,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert "numbering-policy-conflict:expected-aired:observed-special" in (
        result.assignments[0].evidence.reasons
    )
    assert provider.catalog_calls == 0
