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
    *,
    errors: tuple[str, ...] = (),
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog-{identity.value}",
        show_identity=identity,
        episodes=episodes,
        errors=errors,
    )


def _unresolved_catalog(identity: ProviderIdentity) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog-{identity.value}-failure",
        show_identity=identity,
        episodes=(),
        unresolved_reason="fixture-provider-failure",
    )


class MixedSegmentProvider:
    provider_name = "fixture"

    def __init__(
        self,
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.catalogs = dict(catalogs)
        self.catalog_calls: list[ProviderIdentity] = []
        self.shows = (
            ProviderShow(ALPHA, "Example Collection", None),
            ProviderShow(BETA, "Example Collection", None),
        )

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search:{title}:v1",
            shows=self.shows,
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        return self.catalogs[show_identity]


def _mixed_parses() -> tuple[ParseResult, ...]:
    return (
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(1,),
            segment_hint="a",
            title_hint="First Story",
        ),
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(1,),
            segment_hint="b",
            title_hint="Second Story",
        ),
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(2,),
            title_hint="Full Episode",
        ),
    )


def _resolve(
    provider: MixedSegmentProvider,
    parses: tuple[ParseResult, ...] | None = None,
):
    return resolve_show_group_with_provider(
        "Example Collection",
        parses or _mixed_parses(),
        load_overrides(),
        provider,
    )


def test_mixed_aired_segment_titles_break_show_identity_tie() -> None:
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(
                ALPHA,
                (
                    _episode("alpha", "one", 1, 1, "First Story"),
                    _episode("alpha", "two", 1, 2, "Second Story"),
                    _episode("alpha", "three", 1, 3, "Full Episode"),
                ),
            ),
            BETA: _catalog(
                BETA,
                (
                    _episode("beta", "one", 1, 1, "First Story"),
                    _episode("beta", "other", 1, 2, "Unrelated Story"),
                ),
            ),
        }
    )

    result = _resolve(provider)

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert (
        "mixed-segment-title-rescue:unique-compatible-candidate"
        in result.evidence.reasons
    )
    assert (
        "mixed-segment-title-rescue-winner:fixture:alpha" in result.evidence.reasons
    )
    assert provider.catalog_calls == [ALPHA, BETA]


def test_mixed_segment_rescue_requires_two_distinct_titles() -> None:
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(
                ALPHA, (_episode("alpha", "one", 1, 1, "First Story"),)
            ),
            BETA: _catalog(
                BETA, (_episode("beta", "one", 1, 1, "First Story"),)
            ),
        }
    )
    parses = (
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(1,),
            segment_hint="a",
            title_hint="First Story",
        ),
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(2,),
            title_hint="Full Episode",
        ),
    )

    result = _resolve(provider, parses)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert provider.catalog_calls == []


def test_mixed_segment_rescue_rejects_duplicate_normalized_titles() -> None:
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(
                ALPHA, (_episode("alpha", "one", 1, 1, "First Story"),)
            ),
            BETA: _catalog(
                BETA, (_episode("beta", "one", 1, 1, "First Story"),)
            ),
        }
    )
    parses = (
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(1,),
            segment_hint="a",
            title_hint="First Story",
        ),
        ParseResult(
            series_hint="Example Collection",
            season=1,
            episodes=(1,),
            segment_hint="b",
            title_hint="First.Story",
        ),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,)),
    )

    result = _resolve(provider, parses)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert provider.catalog_calls == []


def test_mixed_segment_rescue_keeps_equal_catalog_matches_blocked() -> None:
    episodes = (
        _episode("shared", "one", 1, 1, "First Story"),
        _episode("shared", "two", 1, 2, "Second Story"),
    )
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(ALPHA, episodes),
            BETA: _catalog(BETA, episodes),
        }
    )

    result = _resolve(provider)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "mixed-segment-title-rescue:no-unique-compatible-candidate"
        in result.evidence.reasons
    )


def test_mixed_segment_rescue_keeps_provider_failure_indeterminate() -> None:
    provider = MixedSegmentProvider(
        {
            ALPHA: _unresolved_catalog(ALPHA),
            BETA: _catalog(
                BETA,
                (
                    _episode("beta", "one", 1, 1, "First Story"),
                    _episode("beta", "two", 1, 2, "Second Story"),
                ),
            ),
        }
    )

    result = _resolve(provider)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "mixed-segment-title-rescue:indeterminate-candidate-catalog"
        in result.evidence.reasons
    )


def test_mixed_segment_rescue_requires_unique_provider_title_matches() -> None:
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(
                ALPHA,
                (
                    _episode("alpha", "one", 1, 1, "First Story"),
                    _episode("alpha", "duplicate", 2, 1, "First Story"),
                    _episode("alpha", "two", 1, 2, "Second Story"),
                ),
            ),
            BETA: _catalog(
                BETA,
                (
                    _episode("beta", "one", 1, 1, "First Story"),
                    _episode("beta", "two", 1, 2, "Second Story"),
                ),
            ),
        }
    )

    result = _resolve(provider)

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == BETA
    alpha = next(
        candidate
        for candidate in result.evidence.candidates
        if candidate.provider_identity == ALPHA
    )
    assert "mixed-segment-title-ambiguous:first story" in alpha.reasons


def test_mixed_segment_rescue_is_input_order_deterministic() -> None:
    catalogs = {
        ALPHA: _catalog(
            ALPHA,
            (
                _episode("alpha", "one", 1, 1, "First Story"),
                _episode("alpha", "two", 1, 2, "Second Story"),
            ),
        ),
        BETA: _catalog(
            BETA, (_episode("beta", "other", 1, 1, "Unrelated"),)
        ),
    }
    first = _resolve(MixedSegmentProvider(catalogs), _mixed_parses())
    second = _resolve(MixedSegmentProvider(catalogs), tuple(reversed(_mixed_parses())))

    assert first == second
