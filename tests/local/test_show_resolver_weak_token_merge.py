from __future__ import annotations

from collections.abc import Mapping

import pytest

from jellyfin_show_organizer.models import ParseResult, ProviderIdentity
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)
from jellyfin_show_organizer.show_resolver import (
    ResolutionStatus,
    resolve_show_group_with_provider,
)

pytestmark = pytest.mark.local


def _identity(value: str) -> ProviderIdentity:
    return ProviderIdentity("fixture", value)


def _episode(show: str, season: int, number: int) -> ProviderEpisode:
    return ProviderEpisode(
        identity=_identity(f"{show}-{season}-{number}"),
        season=season,
        number=number,
        title=f"Episode {number}",
    )


def _catalog(
    show: str,
    coordinates: tuple[tuple[int, int], ...],
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{show}",
        cache_snapshot_id=f"catalog:{show}",
        show_identity=_identity(show),
        episodes=tuple(
            _episode(show, season, number) for season, number in coordinates
        ),
    )


class Provider:
    provider_name = "fixture"

    def __init__(
        self,
        searches: Mapping[str, tuple[ProviderShow, ...]],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog] | None = None,
        *,
        unresolved_searches: frozenset[str] = frozenset(),
    ) -> None:
        self.searches = dict(searches)
        self.catalogs = dict(catalogs or {})
        self.unresolved_searches = unresolved_searches
        self.search_calls: list[str] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        unresolved = title in self.unresolved_searches
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search:{len(self.search_calls)}",
            shows=() if unresolved else self.searches.get(title, ()),
            unresolved_reason="fixture-search-unresolved" if unresolved else None,
        )

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        return self.catalogs[show_identity]


def _aired_group() -> tuple[ParseResult, ...]:
    return (
        ParseResult(series_hint="Example Hero", season=1, episodes=(1,)),
        ParseResult(series_hint="Example Hero", season=1, episodes=(2,)),
    )


def test_weak_exact_candidates_can_trigger_compacted_discovery_and_catalog_rescue() -> (
    None
):
    noise = ProviderShow(_identity("noise"), "Harbor Patrol", None)
    hero = ProviderShow(_identity("hero"), "ExampleHero Adventures", None)
    provider = Provider(
        searches={
            "Example Hero": (noise,),
            "ExampleHero": (hero,),
        },
        catalogs={
            _identity("noise"): _catalog("noise", ((9, 9),)),
            _identity("hero"): _catalog("hero", ((1, 1), (1, 2))),
        },
    )

    result = resolve_show_group_with_provider(
        "Example Hero",
        _aired_group(),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("hero")
    assert "provider-search-token-merge-trigger:weak-exact-candidates" in (
        result.evidence.reasons
    )
    assert "provider-search-token-merge:complete" in result.evidence.reasons
    assert "aired-catalog-rescue:unique-compatible-candidate" in (
        result.evidence.reasons
    )
    assert provider.search_calls == ["Example Hero", "Example Hero", "ExampleHero"]


def test_strong_exact_candidate_does_not_trigger_compacted_retry() -> None:
    hero = ProviderShow(_identity("hero"), "Example Hero", None)
    provider = Provider(
        searches={
            "Example Hero": (hero,),
            "ExampleHero": (ProviderShow(_identity("other"), "Other Show", None),),
        }
    )

    result = resolve_show_group_with_provider(
        "Example Hero",
        (ParseResult(series_hint="Example Hero", season=1, episodes=(1,)),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("hero")
    assert provider.search_calls == ["Example Hero"]


def test_suspicious_exact_candidate_does_not_trigger_compacted_retry() -> None:
    candidate = ProviderShow(
        _identity("subtitle"),
        "Example Hero: Adventures",
        None,
    )
    provider = Provider(
        searches={
            "Example Hero": (candidate,),
            "ExampleHero": (ProviderShow(_identity("other"), "Other Show", None),),
        }
    )

    result = resolve_show_group_with_provider(
        "Example Hero",
        (ParseResult(series_hint="Example Hero", season=1, episodes=(1,)),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert provider.search_calls == ["Example Hero"]


def test_unresolved_compacted_search_fails_closed() -> None:
    noise = ProviderShow(_identity("noise"), "Harbor Patrol", None)
    provider = Provider(
        searches={"Example Hero": (noise,)},
        catalogs={_identity("noise"): _catalog("noise", ((9, 9),))},
        unresolved_searches=frozenset({"ExampleHero"}),
    )

    result = resolve_show_group_with_provider(
        "Example Hero",
        _aired_group(),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "provider-search-token-merge:indeterminate" in result.evidence.reasons
    assert "fixture-search-unresolved" in result.evidence.reasons


def test_conflicting_candidate_metadata_from_compacted_search_fails_closed() -> None:
    provider = Provider(
        searches={
            "Example Hero": (ProviderShow(_identity("shared"), "Harbor Patrol", None),),
            "ExampleHero": (
                ProviderShow(_identity("shared"), "ExampleHero Adventures", None),
            ),
        },
        catalogs={_identity("shared"): _catalog("shared", ((9, 9),))},
    )

    result = resolve_show_group_with_provider(
        "Example Hero",
        _aired_group(),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "provider-search-token-merge:conflicting-candidate-metadata"
        in result.evidence.reasons
    )
