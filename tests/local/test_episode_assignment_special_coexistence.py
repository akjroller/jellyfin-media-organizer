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

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
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


def test_s00e01_is_not_assigned_by_aired_numbering() -> None:
    provider = FixtureProvider(
        (
            _episode(
                "special-1", season=0, number=1, title="Preview", episode_type="special"
            ),
        )
    )

    result = assign_episode_group_with_provider(
        _show(),
        (
            SourceEpisodeInput(
                "preview.mkv",
                ParseResult(season=0, episodes=(1,), title_hint="Preview"),
            ),
        ),
        provider,
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert assignment.episodes == ()
    assert (
        "numbering-policy-conflict:expected-aired:observed-special"
        in assignment.evidence.reasons
    )


def test_special_title_fallback_uses_unique_provider_special() -> None:
    provider = FixtureProvider(
        (
            _episode(
                "special-7",
                season=0,
                number=7,
                title="Moon Flight",
                episode_type="special",
            ),
        )
    )

    result = assign_episode_group_with_provider(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "moon.mkv",
                ParseResult(
                    special_kind="ova",
                    special_episode=1,
                    title_hint="Moon Flight",
                ),
            ),
        ),
        provider,
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity.value == "special-7"
    assert "special-fallback-title-match:moon flight" in assignment.evidence.reasons


def test_special_airdate_fallback_uses_unique_special() -> None:
    provider = FixtureProvider(
        (
            _episode(
                "special-2",
                season=0,
                number=2,
                title="Winter OAD",
                episode_type="special",
            ),
        )
    )
    provider.episodes = (
        provider.episodes[0].__class__(
            identity=provider.episodes[0].identity,
            season=0,
            number=2,
            title="Winter OAD",
            episode_type="special",
            airdate="2024-01-02",
        ),
    )

    result = assign_episode_group_with_provider(
        _show(NumberingMode.SPECIAL),
        (SourceEpisodeInput("winter.mkv", ParseResult(episode_date="2024-01-02")),),
        provider,
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity.value == "special-2"
    assert "catalog-special-airdate-fallback:unique" in assignment.evidence.reasons


def test_special_title_fallback_remains_suspicious_when_ambiguous() -> None:
    provider = FixtureProvider(
        (
            _episode(
                "special-a",
                season=0,
                number=1,
                title="Bonus Flight",
                episode_type="special",
            ),
            _episode(
                "special-b",
                season=0,
                number=2,
                title="Bonus Flight",
                episode_type="special",
            ),
        )
    )

    result = assign_episode_group_with_provider(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "bonus.mkv",
                ParseResult(
                    special_kind="ova",
                    special_episode=9,
                    title_hint="Bonus Flight",
                ),
            ),
        ),
        provider,
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert (
        "special-fallback-title-ambiguous:bonus flight" in assignment.evidence.reasons
    )


def test_generic_special_title_does_not_select_a_catalog_entry() -> None:
    provider = FixtureProvider(
        (
            _episode(
                "special-1",
                season=0,
                number=1,
                title="Bonus",
                episode_type="special",
            ),
        )
    )

    result = assign_episode_group_with_provider(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "bonus.mkv",
                ParseResult(special_kind="ova", special_episode=9, title_hint="Bonus"),
            ),
        ),
        provider,
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert assignment.episodes == ()


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
