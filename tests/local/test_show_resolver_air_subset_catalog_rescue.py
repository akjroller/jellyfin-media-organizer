from __future__ import annotations

from collections.abc import Mapping

import pytest

from jellyfin_show_organizer.models import NumberingMode, ParseResult, ProviderIdentity
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


def _catalog(show: str) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{show}",
        cache_snapshot_id=f"catalog:{show}",
        show_identity=_identity(show),
        episodes=(
            _episode(show, 1, 1),
            _episode(show, 1, 2),
            _episode(show, 1, 3),
        ),
    )


class Provider:
    provider_name = "fixture"

    def __init__(
        self,
        searches: Mapping[str, tuple[ProviderShow, ...]],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.searches = dict(searches)
        self.catalogs = dict(catalogs)

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search:{title}",
            shows=self.searches.get(title, ()),
        )

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        return self.catalogs[show_identity]


def _mixed_group() -> tuple[ParseResult, ...]:
    return (
        ParseResult(
            series_hint="Example Frontier",
            season=1,
            episodes=(1,),
            year=2024,
        ),
        ParseResult(
            series_hint="Example Frontier",
            season=1,
            episodes=(2,),
            year=2024,
        ),
        ParseResult(
            series_hint="Example Frontier",
            absolute_episode=99,
            year=2024,
        ),
    )


def test_two_clean_aired_coordinates_can_rescue_with_independent_absolute_sibling() -> (
    None
):
    show = ProviderShow(
        _identity("frontier"),
        "Example Frontier: The Long Journey",
        2024,
    )
    result = resolve_show_group_with_provider(
        "Example Frontier",
        _mixed_group(),
        load_overrides(),
        Provider(
            searches={"Example Frontier": (show,)},
            catalogs={_identity("frontier"): _catalog("frontier")},
        ),
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("frontier")
    assert result.show.numbering_mode is NumberingMode.AIRED
    assert result.evidence.confidence == 0.88
    assert "aired-catalog-rescue:unique-compatible-candidate" in result.evidence.reasons


def test_one_aired_coordinate_plus_absolute_sibling_is_not_enough_for_rescue() -> None:
    show = ProviderShow(
        _identity("frontier"),
        "Example Frontier: The Long Journey",
        2024,
    )
    result = resolve_show_group_with_provider(
        "Example Frontier",
        (
            ParseResult(
                series_hint="Example Frontier",
                season=1,
                episodes=(1,),
                year=2024,
            ),
            ParseResult(
                series_hint="Example Frontier",
                absolute_episode=99,
                year=2024,
            ),
        ),
        load_overrides(),
        Provider(
            searches={"Example Frontier": (show,)},
            catalogs={_identity("frontier"): _catalog("frontier")},
        ),
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "aired-catalog-rescue:unique-compatible-candidate"
        not in result.evidence.reasons
    )


def test_mixed_aired_and_absolute_evidence_on_same_record_blocks_subset_rescue() -> (
    None
):
    show = ProviderShow(
        _identity("frontier"),
        "Example Frontier: The Long Journey",
        2024,
    )
    result = resolve_show_group_with_provider(
        "Example Frontier",
        (
            *_mixed_group()[:2],
            ParseResult(
                series_hint="Example Frontier",
                season=1,
                episodes=(3,),
                absolute_episode=3,
                year=2024,
            ),
        ),
        load_overrides(),
        Provider(
            searches={"Example Frontier": (show,)},
            catalogs={_identity("frontier"): _catalog("frontier")},
        ),
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "aired-catalog-rescue:unique-compatible-candidate"
        not in result.evidence.reasons
    )


def test_two_catalogs_that_both_fit_aired_subset_remain_suspicious() -> None:
    alpha = ProviderShow(
        _identity("alpha"),
        "Example Frontier: Alpha",
        2024,
    )
    beta = ProviderShow(
        _identity("beta"),
        "Example Frontier: Beta",
        2024,
    )
    result = resolve_show_group_with_provider(
        "Example Frontier",
        _mixed_group(),
        load_overrides(),
        Provider(
            searches={"Example Frontier": (alpha, beta)},
            catalogs={
                _identity("alpha"): _catalog("alpha"),
                _identity("beta"): _catalog("beta"),
            },
        ),
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "aired-catalog-rescue:no-unique-compatible-candidate" in result.evidence.reasons
    )
