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

ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


def _episode(show: str, season: int, number: int) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", f"{show}-{season}-{number}"),
        season=season,
        number=number,
        title=f"Episode {season}-{number}",
    )


def _catalog(
    identity: ProviderIdentity,
    episodes: tuple[ProviderEpisode, ...],
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog-{identity.value}",
        show_identity=identity,
        episodes=episodes,
    )


class BackoffProvider:
    provider_name = "fixture"

    def __init__(
        self,
        searches: Mapping[str, tuple[ProviderShow, ...]],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
        *,
        unresolved_query: str | None = None,
    ) -> None:
        self.searches = dict(searches)
        self.catalogs = dict(catalogs)
        self.unresolved_query = unresolved_query
        self.search_calls: list[str] = []
        self.catalog_calls: list[ProviderIdentity] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        if title == self.unresolved_query:
            return ProviderSearchSnapshot(
                provider="fixture",
                request_key=f"search:{title}",
                cache_snapshot_id=f"search-{len(self.search_calls)}",
                shows=(),
                unresolved_reason="fixture-provider-failure",
            )
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search-{len(self.search_calls)}",
            shows=self.searches.get(title, ()),
        )

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        return self.catalogs[show_identity]


def test_empty_exact_search_uses_prefixes_only_for_candidate_discovery() -> None:
    alpha = ProviderShow(ALPHA, "Different Alpha", None)
    beta = ProviderShow(BETA, "Different Beta", None)
    provider = BackoffProvider(
        searches={
            "Example Extended": (alpha, beta),
            "Example": (alpha, beta),
        },
        catalogs={
            ALPHA: _catalog(ALPHA, (_episode("alpha", 1, 1),)),
            BETA: _catalog(BETA, (_episode("beta", 2, 5),)),
        },
    )

    result = resolve_show_group_with_provider(
        "Example Extended Edition",
        (
            ParseResult(
                series_hint="Example Extended Edition",
                season=2,
                episodes=(5,),
            ),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == BETA
    assert result.evidence.method.endswith("+search-backoff+catalog-rescue")
    assert "provider-search-backoff:attempted" in result.evidence.reasons
    assert "provider-search-backoff:complete" in result.evidence.reasons
    assert provider.search_calls == [
        "Example Extended Edition",
        "Example Extended",
        "Example",
    ]
    assert set(provider.catalog_calls) == {ALPHA, BETA}


def test_search_backoff_does_not_lower_match_threshold() -> None:
    candidate = ProviderShow(ALPHA, "Different Primary", None)
    provider = BackoffProvider(
        searches={"Example Extended": (candidate,), "Example": (candidate,)},
        catalogs={
            ALPHA: _catalog(ALPHA, (_episode("alpha", 1, 1),)),
        },
    )

    result = resolve_show_group_with_provider(
        "Example Extended Edition",
        (
            ParseResult(
                series_hint="Example Extended Edition",
                season=8,
                episodes=(12,),
            ),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "provider-evidence-below-threshold" in result.evidence.reasons


def test_provider_failure_during_backoff_fails_closed() -> None:
    provider = BackoffProvider(
        searches={},
        catalogs={},
        unresolved_query="Example Extended",
    )

    result = resolve_show_group_with_provider(
        "Example Extended Edition",
        (ParseResult(series_hint="Example Extended Edition"),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "provider-search-backoff:indeterminate" in result.evidence.reasons
    assert "fixture-provider-failure" in result.evidence.reasons
    assert provider.search_calls == ["Example Extended Edition", "Example Extended"]


def test_single_token_title_does_not_generate_search_backoff() -> None:
    provider = BackoffProvider(searches={}, catalogs={})

    result = resolve_show_group_with_provider(
        "Example",
        (ParseResult(series_hint="Example"),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert result.evidence.reasons[-1] == "no-valid-provider-candidates"
    assert provider.search_calls == ["Example"]
