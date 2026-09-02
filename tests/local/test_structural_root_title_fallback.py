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
from jellyfin_show_organizer.structural_root_title_fallback import structural_root_title

pytestmark = pytest.mark.local

ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


def _show(identity: ProviderIdentity, title: str, year: int = 2024) -> ProviderShow:
    return ProviderShow(identity, title, year)


def _episode(identity: str, season: int, number: int) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", identity),
        season=season,
        number=number,
        title=f"Episode {number}",
    )


def _snapshot(title: str, *shows: ProviderShow) -> ProviderSearchSnapshot:
    return ProviderSearchSnapshot(
        provider="fixture",
        request_key=f"search:{title}",
        cache_snapshot_id=f"snapshot:{title}",
        shows=tuple(shows),
    )


def _failed_snapshot(title: str) -> ProviderSearchSnapshot:
    return ProviderSearchSnapshot(
        provider="fixture",
        request_key=f"search:{title}",
        cache_snapshot_id=f"failure:{title}",
        shows=(),
        unresolved_reason="fixture-provider-failure",
    )


def _catalog(
    identity: ProviderIdentity,
    *episodes: ProviderEpisode,
    unresolved: bool = False,
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog:{identity.value}",
        show_identity=identity,
        episodes=tuple(episodes),
        unresolved_reason="fixture-provider-failure" if unresolved else None,
    )


class RootTitleProvider:
    provider_name = "fixture"

    def __init__(
        self,
        searches: Mapping[str, ProviderSearchSnapshot],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.searches = dict(searches)
        self.catalogs = dict(catalogs)
        self.search_calls: list[str] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return self.searches.get(title, _snapshot(title))

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        return self.catalogs.get(show_identity, _catalog(show_identity))


def _parses(*episodes: int) -> tuple[ParseResult, ...]:
    return tuple(
        ParseResult(series_hint="Example TLA", season=1, episodes=(episode,))
        for episode in episodes
    )


def _resolve(
    provider: RootTitleProvider,
    parses: tuple[ParseResult, ...] | None = None,
    source_key: str = "Example The Long Alias Complete 1080p",
):
    return resolve_show_group_with_provider(
        source_key,
        parses or _parses(1, 2),
        load_overrides(),
        provider,
    )


def _successful_provider() -> RootTitleProvider:
    return RootTitleProvider(
        {
            "Example TLA": _snapshot("Example TLA", _show(BETA, "Example")),
            "Example The Long Alias": _snapshot(
                "Example The Long Alias",
                _show(ALPHA, "Example The Long Alias"),
            ),
        },
        {
            ALPHA: _catalog(
                ALPHA,
                _episode("alpha-1", 1, 1),
                _episode("alpha-2", 1, 2),
            ),
            BETA: _catalog(BETA),
        },
    )


def test_root_title_requires_complete_initialism_expansion() -> None:
    assert (
        structural_root_title(
            "Example The Long Alias Complete 1080p",
            "Example TLA",
        )
        == "Example The Long Alias"
    )
    assert (
        structural_root_title("Example Serial Complete 1080p", "Example Series") is None
    )
    assert structural_root_title("Example TLA Complete 1080p", "Example TLA") is None


def test_catalog_confirmed_root_title_resolves() -> None:
    result = _resolve(_successful_provider())

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert "structural-root-title-fallback:catalog-confirmed" in result.evidence.reasons
    assert (
        "structural-root-title-fallback-winner:fixture:alpha" in result.evidence.reasons
    )


def test_normal_success_bypasses_root_title_fallback() -> None:
    provider = RootTitleProvider(
        {
            "Example TLA": _snapshot(
                "Example TLA",
                _show(ALPHA, "Example TLA"),
            )
        },
        {ALPHA: _catalog(ALPHA, _episode("alpha-1", 1, 1))},
    )

    result = _resolve(provider, _parses(1))

    assert result.status is ResolutionStatus.MATCHED
    assert "Example The Long Alias" not in provider.search_calls
    assert all(
        "structural-root-title-fallback" not in reason
        for reason in result.evidence.reasons
    )


def test_root_provider_failure_stays_unresolved() -> None:
    provider = RootTitleProvider(
        {
            "Example TLA": _snapshot("Example TLA", _show(BETA, "Example")),
            "Example The Long Alias": _failed_snapshot("Example The Long Alias"),
        },
        {},
    )

    result = _resolve(provider)

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "structural-root-title-fallback:root-search-indeterminate"
        in result.evidence.reasons
    )


def test_conflicting_metadata_for_same_identity_stays_unresolved() -> None:
    provider = RootTitleProvider(
        {
            "Example TLA": _snapshot("Example TLA", _show(ALPHA, "Weak Candidate")),
            "Example The Long Alias": _snapshot(
                "Example The Long Alias",
                _show(ALPHA, "Example The Long Alias"),
            ),
        },
        {},
    )

    result = _resolve(provider)

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "structural-root-title-fallback:conflicting-candidate-metadata"
        in result.evidence.reasons
    )


def test_catalog_incompatibility_stays_unresolved() -> None:
    provider = _successful_provider()
    provider.catalogs[ALPHA] = _catalog(ALPHA, _episode("alpha-1", 1, 1))

    result = _resolve(provider)

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "structural-root-title-fallback:catalog-incompatible" in result.evidence.reasons
    )


def test_single_observation_is_not_enough_for_root_rescue() -> None:
    provider = _successful_provider()

    result = _resolve(provider, _parses(1))

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "structural-root-title-fallback:insufficient-catalog-evidence"
        in result.evidence.reasons
    )


def test_retried_ambiguity_remains_blocked() -> None:
    provider = RootTitleProvider(
        {
            "Example TLA": _snapshot("Example TLA", _show(BETA, "Example")),
            "Example The Long Alias": _snapshot(
                "Example The Long Alias",
                _show(ALPHA, "Example The Long Alias"),
                _show(ProviderIdentity("fixture", "gamma"), "Example The Long Alias"),
            ),
        },
        {
            ALPHA: _catalog(
                ALPHA,
                _episode("alpha-1", 1, 1),
                _episode("alpha-2", 1, 2),
            ),
            ProviderIdentity("fixture", "gamma"): _catalog(
                ProviderIdentity("fixture", "gamma"),
                _episode("gamma-1", 1, 1),
                _episode("gamma-2", 1, 2),
            ),
        },
    )

    result = _resolve(provider)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "structural-root-title-fallback:no-unique-root-match" in result.evidence.reasons
    )


def test_candidate_union_is_deterministic() -> None:
    first = _successful_provider()
    second = RootTitleProvider(
        {
            "Example TLA": _snapshot("Example TLA", _show(BETA, "Example")),
            "Example The Long Alias": _snapshot(
                "Example The Long Alias",
                _show(ALPHA, "Example The Long Alias"),
            ),
        },
        dict(reversed(tuple(first.catalogs.items()))),
    )

    assert _resolve(first) == _resolve(second)
