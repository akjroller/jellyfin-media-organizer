from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

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
    resolve_show_group,
    resolve_show_group_with_provider,
)
from jellyfin_show_organizer.tvmaze_cache import (
    TVMAZE_EPISODES_URL,
    TVMAZE_SEARCH_URL,
    TvmazeCatalogCache,
)

pytestmark = pytest.mark.local


class StaticProvider:
    provider_name = "fixture"

    def __init__(
        self,
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.catalogs = dict(catalogs)
        self.search_calls: list[str] = []
        self.catalog_calls: list[ProviderIdentity] = []
        self.shows = (
            ProviderShow(ProviderIdentity("fixture", "alpha"), "Example Series", None),
            ProviderShow(ProviderIdentity("fixture", "beta"), "Example Series", None),
        )

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return ProviderSearchSnapshot(
            provider=self.provider_name,
            request_key="search:example-series",
            cache_snapshot_id="search-v1",
            shows=self.shows,
        )

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        return self.catalogs[show_identity]


def _episode(
    show_value: str,
    episode_value: str,
    season: int,
    number: int,
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", f"{show_value}-{episode_value}"),
        season=season,
        number=number,
        title=f"Episode {episode_value}",
    )


def _catalog(
    show_value: str,
    episodes: tuple[ProviderEpisode, ...],
    *,
    errors: tuple[str, ...] = (),
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{show_value}",
        cache_snapshot_id=f"catalog-{show_value}-v1",
        show_identity=ProviderIdentity("fixture", show_value),
        episodes=episodes,
        errors=errors,
    )


def _unresolved_catalog(show_value: str) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{show_value}",
        cache_snapshot_id=f"catalog-{show_value}-failure",
        show_identity=ProviderIdentity("fixture", show_value),
        episodes=(),
        unresolved_reason="fixture-provider-failure",
    )


def _resolve(provider: StaticProvider):
    return resolve_show_group_with_provider(
        "Example Series",
        (
            ParseResult(series_hint="Example Series", season=1, episodes=(1,)),
            ParseResult(series_hint="Example Series", season=8, episodes=(12,)),
        ),
        load_overrides(),
        provider,
    )


def test_unique_coordinate_compatible_candidate_breaks_exact_title_tie() -> None:
    alpha_episodes = tuple(
        _episode("alpha", str(number), 1, number) for number in range(1, 13)
    )
    beta_episodes = (
        _episode("beta", "one", 1, 1),
        _episode("beta", "target", 8, 12),
    )
    provider = StaticProvider(
        {
            ProviderIdentity("fixture", "alpha"): _catalog("alpha", alpha_episodes),
            ProviderIdentity("fixture", "beta"): _catalog("beta", beta_episodes),
        }
    )

    resolution = _resolve(provider)

    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.provider_identity == ProviderIdentity("fixture", "beta")
    assert resolution.evidence.method == "fixture-search+catalog-tiebreak"
    assert "catalog-tiebreak:unique-compatible-candidate" in resolution.evidence.reasons
    assert "catalog-tiebreak-winner:fixture:beta" in resolution.evidence.reasons
    assert provider.catalog_calls == [
        ProviderIdentity("fixture", "alpha"),
        ProviderIdentity("fixture", "beta"),
    ]
    by_identity = {
        candidate.provider_identity: candidate
        for candidate in resolution.evidence.candidates
    }
    assert (
        "catalog-missing:S08E12"
        in by_identity[ProviderIdentity("fixture", "alpha")].reasons
    )
    assert (
        "catalog-compatible:true:aired"
        in by_identity[ProviderIdentity("fixture", "beta")].reasons
    )


def test_equal_catalog_compatibility_remains_suspicious() -> None:
    episodes = (
        _episode("shared", "one", 1, 1),
        _episode("shared", "target", 8, 12),
    )
    provider = StaticProvider(
        {
            ProviderIdentity("fixture", "alpha"): _catalog("alpha", episodes),
            ProviderIdentity("fixture", "beta"): _catalog("beta", episodes),
        }
    )

    resolution = _resolve(provider)

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None
    assert (
        "catalog-tiebreak:no-unique-compatible-candidate" in resolution.evidence.reasons
    )


def test_missing_coordinates_for_every_candidate_remain_suspicious() -> None:
    provider = StaticProvider(
        {
            ProviderIdentity("fixture", "alpha"): _catalog(
                "alpha", (_episode("alpha", "one", 1, 1),)
            ),
            ProviderIdentity("fixture", "beta"): _catalog(
                "beta", (_episode("beta", "one", 1, 1),)
            ),
        }
    )

    resolution = _resolve(provider)

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None
    assert (
        "catalog-tiebreak:no-unique-compatible-candidate" in resolution.evidence.reasons
    )


def test_provider_catalog_failure_cannot_be_used_to_eliminate_candidate() -> None:
    provider = StaticProvider(
        {
            ProviderIdentity("fixture", "alpha"): _unresolved_catalog("alpha"),
            ProviderIdentity("fixture", "beta"): _catalog(
                "beta",
                (
                    _episode("beta", "one", 1, 1),
                    _episode("beta", "target", 8, 12),
                ),
            ),
        }
    )

    resolution = _resolve(provider)

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None
    assert (
        "catalog-tiebreak:indeterminate-candidate-catalog"
        in resolution.evidence.reasons
    )


def test_malformed_required_catalog_metadata_keeps_tie_indeterminate() -> None:
    provider = StaticProvider(
        {
            ProviderIdentity("fixture", "alpha"): _catalog(
                "alpha",
                (_episode("alpha", "one", 1, 1),),
                errors=("invalid-catalog-season:1",),
            ),
            ProviderIdentity("fixture", "beta"): _catalog(
                "beta",
                (
                    _episode("beta", "one", 1, 1),
                    _episode("beta", "target", 8, 12),
                ),
            ),
        }
    )

    resolution = _resolve(provider)

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None
    assert (
        "catalog-tiebreak:indeterminate-candidate-catalog"
        in resolution.evidence.reasons
    )


def test_mixed_numbering_evidence_does_not_trigger_catalog_tiebreak() -> None:
    provider = StaticProvider(
        {
            ProviderIdentity("fixture", "alpha"): _catalog("alpha", ()),
            ProviderIdentity("fixture", "beta"): _catalog("beta", ()),
        }
    )

    resolution = resolve_show_group_with_provider(
        "Example Series",
        (
            ParseResult(
                series_hint="Example Series",
                season=1,
                episodes=(1,),
                absolute_episode=1,
            ),
        ),
        load_overrides(),
        provider,
    )

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert provider.catalog_calls == []
    assert all(
        not reason.startswith("catalog-tiebreak:")
        for reason in resolution.evidence.reasons
    )


class TvmazeFixtureGetter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, dict(params or {})))
        if url == TVMAZE_SEARCH_URL:
            return [
                {"show": {"id": 101, "name": "Example Series"}},
                {"show": {"id": 202, "name": "Example Series"}},
            ]
        if url == TVMAZE_EPISODES_URL.format(tvmaze_id=101):
            return [
                {"id": 1001, "season": 1, "number": 1, "name": "One"},
            ]
        if url == TVMAZE_EPISODES_URL.format(tvmaze_id=202):
            return [
                {"id": 2001, "season": 1, "number": 1, "name": "One"},
                {"id": 2012, "season": 8, "number": 12, "name": "Target"},
            ]
        raise AssertionError(f"unexpected provider request: {url}")


def test_warmed_catalog_tiebreak_replays_without_http(tmp_path: Path) -> None:
    cache = TvmazeCatalogCache(tmp_path / "cache")
    parses = (
        ParseResult(series_hint="Example Series", season=1, episodes=(1,)),
        ParseResult(series_hint="Example Series", season=8, episodes=(12,)),
    )
    cold_getter = TvmazeFixtureGetter()
    first = resolve_show_group(
        "Example Series",
        parses,
        load_overrides(),
        cache,
        cold_getter,
    )

    warm_calls: list[str] = []

    def reject_network(
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        del params
        warm_calls.append(url)
        raise AssertionError("warm catalog tie-break attempted provider HTTP")

    second = resolve_show_group(
        "Example Series",
        parses,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        reject_network,
    )

    assert first == second
    assert first.status is ResolutionStatus.MATCHED
    assert first.show is not None
    assert first.show.tvmaze_id == 202
    assert len(cold_getter.calls) == 3
    assert warm_calls == []
