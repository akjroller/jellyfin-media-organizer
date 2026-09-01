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

ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


def _episode(
    show: str, value: str, season: int, number: int, title: str
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", f"{show}-{value}"),
        season=season,
        number=number,
        title=title,
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


class SegmentProvider:
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
            cache_snapshot_id=f"search:{title}:v1",
            shows=self.searches.get(title, ()),
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        return self.catalogs[show_identity]


def _segment_parses() -> tuple[ParseResult, ...]:
    return (
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(3,),
            segment_hint="a",
            title_hint="First Story",
        ),
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(3,),
            segment_hint="b",
            title_hint="Second Story",
        ),
    )


def test_segment_titles_can_uniquely_rescue_show_identity() -> None:
    shows = (
        ProviderShow(ALPHA, "Different Alpha", None),
        ProviderShow(BETA, "Different Beta", None),
    )
    provider = SegmentProvider(
        searches={"Example": shows},
        catalogs={
            ALPHA: _catalog(
                ALPHA,
                (
                    _episode("alpha", "one", 1, 1, "First Story"),
                    _episode("alpha", "two", 1, 2, "Second Story"),
                ),
            ),
            BETA: _catalog(
                BETA,
                (_episode("beta", "other", 1, 1, "Unrelated Story"),),
            ),
        },
    )

    result = resolve_show_group_with_provider(
        "Example Collection",
        _segment_parses(),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert result.show.numbering_mode is NumberingMode.SEGMENT_TITLE
    assert result.evidence.method.endswith("+search-backoff+catalog-rescue")
    assert "catalog-rescue-numbering-mode:segment-title" in result.evidence.reasons


def test_segment_rescue_requires_each_title_to_be_unique_in_candidate_catalog() -> None:
    show = ProviderShow(ALPHA, "Different Alpha", None)
    provider = SegmentProvider(
        searches={"Example": (show,)},
        catalogs={
            ALPHA: _catalog(
                ALPHA,
                (
                    _episode("alpha", "one", 1, 1, "First Story"),
                    _episode("alpha", "duplicate", 2, 1, "First Story"),
                    _episode("alpha", "two", 1, 2, "Second Story"),
                ),
            ),
        },
    )

    result = resolve_show_group_with_provider(
        "Example Collection",
        _segment_parses(),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "catalog-rescue:no-unique-compatible-candidate" in result.evidence.reasons
    assert any(
        "segment-catalog-ambiguous-title:first story" in reason
        for candidate in result.evidence.candidates
        for reason in candidate.reasons
    )
