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


def _episode(show: str, season: int, number: int, title: str) -> ProviderEpisode:
    return ProviderEpisode(
        identity=_identity(f"{show}-{season}-{number}"),
        season=season,
        number=number,
        title=title,
    )


def _catalog(
    show: str,
    episodes: tuple[ProviderEpisode, ...],
    *,
    unresolved: bool = False,
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{show}",
        cache_snapshot_id=f"catalog:{show}",
        show_identity=_identity(show),
        episodes=episodes,
        unresolved_reason="fixture-catalog-unresolved" if unresolved else None,
    )


class StructuralProvider:
    provider_name = "fixture"

    def __init__(
        self,
        searches: Mapping[str, tuple[ProviderShow, ...]],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.searches = dict(searches)
        self.catalogs = dict(catalogs)
        self.search_calls: list[str] = []
        self.catalog_calls: list[ProviderIdentity] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search:{len(self.search_calls)}",
            shows=self.searches.get(title, ()),
        )

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        return self.catalogs[show_identity]


def test_initialism_equivalence_can_resolve_expanded_provider_title() -> None:
    next_generation = ProviderShow(
        _identity("next-generation"),
        "Example Fleet The Next Generation",
        None,
    )
    voyager = ProviderShow(_identity("voyager"), "Example Fleet Voyager", None)
    provider = StructuralProvider(
        searches={"Example Fleet": (next_generation, voyager)},
        catalogs={},
    )

    result = resolve_show_group_with_provider(
        "Example Fleet TNG",
        (ParseResult(series_hint="Example Fleet TNG", season=1, episodes=(1,)),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("next-generation")
    assert any(
        "token-initialism-equivalent" in reason
        for candidate in result.evidence.candidates
        if candidate.provider_identity == _identity("next-generation")
        for reason in candidate.reasons
    )


def test_compacted_token_query_can_discover_candidate_then_catalog_rescue() -> None:
    show = ProviderShow(_identity("hero"), "ExampleHero Adventures", None)
    provider = StructuralProvider(
        searches={"ExampleHero": (show,)},
        catalogs={
            _identity("hero"): _catalog(
                "hero",
                (
                    _episode("hero", 1, 1, "Arrival"),
                    _episode("hero", 1, 2, "Departure"),
                ),
            )
        },
    )

    result = resolve_show_group_with_provider(
        "Example Hero",
        (
            ParseResult(series_hint="Example Hero", season=1, episodes=(1,)),
            ParseResult(series_hint="Example Hero", season=1, episodes=(2,)),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("hero")
    assert "provider-search-token-merge:attempted" in result.evidence.reasons
    assert "aired-catalog-rescue:unique-compatible-candidate" in result.evidence.reasons
    assert provider.search_calls == ["Example Hero", "Example", "ExampleHero"]


def test_low_confidence_season_subtitle_is_rescued_only_by_multi_episode_catalog() -> (
    None
):
    show = ProviderShow(_identity("base"), "Example Academy", None)
    provider = StructuralProvider(
        searches={"Example Academy New": (show,)},
        catalogs={
            _identity("base"): _catalog(
                "base",
                (
                    _episode("base", 2, 1, "Return"),
                    _episode("base", 2, 2, "Challenge"),
                ),
            )
        },
    )

    result = resolve_show_group_with_provider(
        "Example Academy New",
        (
            ParseResult(series_hint="Example Academy New", season=2, episodes=(1,)),
            ParseResult(series_hint="Example Academy New", season=2, episodes=(2,)),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("base")
    assert "aired-catalog-rescue:unique-compatible-candidate" in result.evidence.reasons
    assert result.evidence.confidence < 0.90


def test_single_episode_low_confidence_candidate_is_not_catalog_rescued() -> None:
    show = ProviderShow(_identity("base"), "Example Academy", None)
    provider = StructuralProvider(
        searches={"Example Academy New": (show,)},
        catalogs={
            _identity("base"): _catalog("base", (_episode("base", 2, 1, "Return"),))
        },
    )

    result = resolve_show_group_with_provider(
        "Example Academy New",
        (ParseResult(series_hint="Example Academy New", season=2, episodes=(1,)),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "provider-evidence-below-threshold" in result.evidence.reasons


def test_exact_title_collision_uses_multiple_coordinate_titles_to_break_tie() -> None:
    alpha = ProviderShow(_identity("alpha"), "Example Series", None)
    beta = ProviderShow(_identity("beta"), "Example Series", None)
    provider = StructuralProvider(
        searches={"Example Series": (alpha, beta)},
        catalogs={
            _identity("alpha"): _catalog(
                "alpha",
                (
                    _episode("alpha", 1, 1, "First Light"),
                    _episode("alpha", 1, 2, "Second Wind"),
                ),
            ),
            _identity("beta"): _catalog(
                "beta",
                (
                    _episode("beta", 1, 1, "Different Pilot"),
                    _episode("beta", 1, 2, "Second Wind"),
                ),
            ),
        },
    )

    result = resolve_show_group_with_provider(
        "Example Series",
        (
            ParseResult(
                series_hint="Example Series",
                season=1,
                episodes=(1,),
                title_hint="First Light REPACK",
            ),
            ParseResult(
                series_hint="Example Series",
                season=1,
                episodes=(2,),
                title_hint="Second Wind PROPER",
            ),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("alpha")
    assert (
        "catalog-title-tiebreak:unique-compatible-candidate" in result.evidence.reasons
    )
    assert "catalog-tiebreak-winner:fixture:alpha" in result.evidence.reasons


def test_exact_title_collision_with_only_one_title_observation_stays_suspicious() -> (
    None
):
    alpha = ProviderShow(_identity("alpha"), "Example Series", None)
    beta = ProviderShow(_identity("beta"), "Example Series", None)
    shared = (
        _episode("shared", 1, 1, "First Light"),
        _episode("shared", 1, 2, "Second Wind"),
    )
    provider = StructuralProvider(
        searches={"Example Series": (alpha, beta)},
        catalogs={
            _identity("alpha"): _catalog("alpha", shared),
            _identity("beta"): _catalog("beta", shared),
        },
    )

    result = resolve_show_group_with_provider(
        "Example Series",
        (
            ParseResult(
                series_hint="Example Series",
                season=1,
                episodes=(1,),
                title_hint="First Light",
            ),
            ParseResult(series_hint="Example Series", season=1, episodes=(2,)),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "catalog-tiebreak:no-unique-compatible-candidate" in result.evidence.reasons


def test_unresolved_catalog_blocks_low_confidence_rescue() -> None:
    show = ProviderShow(_identity("base"), "Example Academy", None)
    provider = StructuralProvider(
        searches={"Example Academy New": (show,)},
        catalogs={_identity("base"): _catalog("base", (), unresolved=True)},
    )

    result = resolve_show_group_with_provider(
        "Example Academy New",
        (
            ParseResult(series_hint="Example Academy New", season=2, episodes=(1,)),
            ParseResult(series_hint="Example Academy New", season=2, episodes=(2,)),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "aired-catalog-rescue:indeterminate-candidate-catalog"
        in result.evidence.reasons
    )
