from __future__ import annotations

from collections.abc import Mapping

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from jellyfin_show_organizer.episode_title_evidence import (
    is_composite_title_authoritative_group,
    normalized_episode_title_hint,
)
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


def _episode(show: str, value: str, number: int, title: str) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", f"{show}-{value}"),
        season=1,
        number=number,
        title=title,
    )


def _catalog(
    identity: ProviderIdentity,
    episodes: tuple[ProviderEpisode, ...],
    *,
    unresolved_reason: str | None = None,
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog:{identity.value}:v1",
        show_identity=identity,
        episodes=episodes,
        unresolved_reason=unresolved_reason,
    )


class FixtureProvider:
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


def _parses() -> tuple[ParseResult, ...]:
    return (
        ParseResult(
            series_hint="Example Stories",
            season=1,
            episodes=(1, 2),
            title_hint="First Tale & Second Tale AAC2 0",
        ),
        ParseResult(
            series_hint="Example Stories",
            season=1,
            episodes=(3, 4),
            title_hint="Third Tale & Fourth Tale AAC2 0",
        ),
        ParseResult(
            series_hint="Example Stories",
            season=1,
            episodes=(5,),
            title_hint="Standalone Tale AAC2 0",
        ),
    )


def _alpha_catalog() -> ProviderEpisodeCatalog:
    return _catalog(
        ALPHA,
        (
            _episode("alpha", "one", 1, "First Tale / Second Tale"),
            _episode("alpha", "two", 2, "Third Tale / Fourth Tale"),
            _episode("alpha", "three", 3, "Standalone Tale"),
            _episode("alpha", "five", 5, "Wrong Coordinate Title"),
        ),
    )


def _beta_catalog() -> ProviderEpisodeCatalog:
    return _catalog(
        BETA,
        (
            _episode("beta", "one", 1, "Unrelated One"),
            _episode("beta", "two", 2, "Unrelated Two"),
            _episode("beta", "three", 3, "Unrelated Three"),
        ),
    )


def _provider(
    *,
    alpha: ProviderEpisodeCatalog | None = None,
    beta: ProviderEpisodeCatalog | None = None,
) -> FixtureProvider:
    shows = (
        ProviderShow(ALPHA, "Example Stories", None),
        ProviderShow(BETA, "Example Stories", None),
    )
    return FixtureProvider(
        searches={"Example Stories": shows},
        catalogs={
            ALPHA: alpha or _alpha_catalog(),
            BETA: beta or _beta_catalog(),
        },
    )


def _resolve(parses: tuple[ParseResult, ...], provider: FixtureProvider):
    return resolve_show_group_with_provider(
        "Example Stories Collection",
        parses,
        load_overrides(),
        provider,
    )


def test_composite_titles_rescue_show_and_assign_by_title_not_coordinate() -> None:
    parses = _parses()
    provider = _provider()

    result = _resolve(parses, provider)

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert result.show.numbering_mode is NumberingMode.SEGMENT_TITLE
    assert result.evidence.method.endswith("+composite-aired-title-rescue")
    assert "composite-aired-title-rescue:unique-compatible-candidate" in (
        result.evidence.reasons
    )

    assignment = assign_episode_group_with_provider(
        result.show,
        tuple(
            SourceEpisodeInput(source_key=f"source-{index}", parse=parse)
            for index, parse in enumerate(parses, start=1)
        ),
        provider,
    )

    assert assignment.status is AssignmentStatus.MATCHED
    by_source = {item.source_key: item for item in assignment.assignments}
    assert [episode.number for episode in by_source["source-1"].episodes] == [1]
    assert [episode.number for episode in by_source["source-2"].episodes] == [2]
    assert [episode.number for episode in by_source["source-3"].episodes] == [3]
    assert "title-authoritative-aired-remap" in by_source["source-3"].evidence.reasons
    assert (
        "segment-coordinate-evidence:S01E05" in by_source["source-3"].evidence.reasons
    )


def test_composite_title_cleanup_is_conservative() -> None:
    assert normalized_episode_title_hint("First Tale & Second Tale AAC2 0") == (
        "first tale second tale"
    )
    assert normalized_episode_title_hint("First Tale Director Cut") == (
        "first tale director cut"
    )


def test_non_contiguous_composite_coordinates_do_not_qualify() -> None:
    parses = list(_parses())
    parses[0] = ParseResult(
        series_hint="Example Stories",
        season=1,
        episodes=(1, 3),
        title_hint="First Tale & Second Tale AAC2 0",
    )

    assert not is_composite_title_authoritative_group(tuple(parses))
    result = _resolve(tuple(parses), _provider())
    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None


def test_composite_group_requires_an_explicit_title_separator() -> None:
    parses = list(_parses())
    parses[0] = ParseResult(
        series_hint="Example Stories",
        season=1,
        episodes=(1, 2),
        title_hint="First Tale Second Tale AAC2 0",
    )

    assert not is_composite_title_authoritative_group(tuple(parses))


def test_singleton_only_group_does_not_enable_title_authoritative_mode() -> None:
    parses = tuple(
        ParseResult(
            series_hint="Example Stories",
            season=1,
            episodes=(number,),
            title_hint=f"Standalone {number}",
        )
        for number in (1, 2, 3)
    )

    assert not is_composite_title_authoritative_group(parses)


def test_fuzzy_episode_title_does_not_rescue_candidate() -> None:
    alpha = _catalog(
        ALPHA,
        (
            _episode("alpha", "one", 1, "First Tale / Second Tall"),
            _episode("alpha", "two", 2, "Third Tale / Fourth Tale"),
            _episode("alpha", "three", 3, "Standalone Tale"),
        ),
    )

    result = _resolve(_parses(), _provider(alpha=alpha))

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "composite-aired-title-rescue:no-unique-compatible-candidate" in (
        result.evidence.reasons
    )


def test_ambiguous_provider_episode_title_blocks_rescue() -> None:
    alpha = _catalog(
        ALPHA,
        (
            _episode("alpha", "one-a", 1, "First Tale / Second Tale"),
            _episode("alpha", "one-b", 7, "First Tale / Second Tale"),
            _episode("alpha", "two", 2, "Third Tale / Fourth Tale"),
            _episode("alpha", "three", 3, "Standalone Tale"),
        ),
    )

    result = _resolve(_parses(), _provider(alpha=alpha))

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert any(
        "composite-aired-title-ambiguous:" in reason
        for candidate in result.evidence.candidates
        for reason in candidate.reasons
    )


def test_multiple_compatible_show_catalogs_remain_blocked() -> None:
    beta = _catalog(
        BETA,
        (
            _episode("beta", "one", 1, "First Tale / Second Tale"),
            _episode("beta", "two", 2, "Third Tale / Fourth Tale"),
            _episode("beta", "three", 3, "Standalone Tale"),
        ),
    )

    result = _resolve(_parses(), _provider(beta=beta))

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "composite-aired-title-rescue:no-unique-compatible-candidate" in (
        result.evidence.reasons
    )


def test_provider_catalog_failure_blocks_rescue() -> None:
    unresolved = _catalog(ALPHA, (), unresolved_reason="synthetic outage")

    result = _resolve(_parses(), _provider(alpha=unresolved))

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "composite-aired-title-rescue:indeterminate-candidate-catalog" in (
        result.evidence.reasons
    )


def test_composite_rescue_is_input_order_deterministic() -> None:
    parses = _parses()
    provider = _provider()

    forward = _resolve(parses, provider)
    reverse = _resolve(tuple(reversed(parses)), provider)

    assert forward.status == reverse.status
    assert forward.show == reverse.show
    assert forward.evidence.reasons == reverse.evidence.reasons
    assert forward.evidence.candidates == reverse.evidence.candidates
