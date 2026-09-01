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


def _catalog(show: str) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{show}",
        cache_snapshot_id=f"catalog:{show}",
        show_identity=_identity(show),
        episodes=(
            ProviderEpisode(
                identity=_identity(f"episode-{show}"),
                season=1,
                number=1,
                title="Pilot",
            ),
        ),
    )


class FixtureProvider:
    provider_name = "fixture"

    def __init__(
        self,
        shows: tuple[ProviderShow, ...],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.shows = shows
        self.catalogs = dict(catalogs)
        self.search_calls: list[str] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search:{len(self.search_calls)}",
            shows=self.shows,
        )

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        return self.catalogs[show_identity]


def _provider(*shows: ProviderShow) -> FixtureProvider:
    return FixtureProvider(
        tuple(shows),
        {show.identity: _catalog(show.identity.value) for show in shows},
    )


def _parse() -> ParseResult:
    return ParseResult(series_hint="Example Mystery", season=1, episodes=(1,))


def test_root_year_range_breaks_same_title_tie_without_score_boost() -> None:
    original = ProviderShow(_identity("original"), "Example Mystery", 2004)
    remake = ProviderShow(_identity("remake"), "Example Mystery", 2014)
    provider = _provider(original, remake)

    result = resolve_show_group_with_provider(
        "Example Mystery Complete Collection (2004-2010)",
        (_parse(),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("original")
    assert result.show.year == 2004
    assert "structural-source-year-range:2004-2010" in result.evidence.reasons
    assert (
        "structural-year-tiebreak:unique-compatible-candidate"
        in result.evidence.reasons
    )
    assert {candidate.score for candidate in result.evidence.candidates} == {0.9}
    assert provider.search_calls == ["Example Mystery", "Example Mystery"]


def test_root_year_range_stays_ambiguous_when_multiple_candidates_fit() -> None:
    first = ProviderShow(_identity("first"), "Example Mystery", 2004)
    second = ProviderShow(_identity("second"), "Example Mystery", 2008)
    provider = _provider(first, second)

    result = resolve_show_group_with_provider(
        "Example Mystery Archive (2004-2010)",
        (_parse(),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "ambiguous-top-candidates" in result.evidence.reasons


def test_root_year_range_stays_ambiguous_when_contender_year_is_missing() -> None:
    dated = ProviderShow(_identity("dated"), "Example Mystery", 2004)
    unknown = ProviderShow(_identity("unknown"), "Example Mystery", None)
    provider = _provider(dated, unknown)

    result = resolve_show_group_with_provider(
        "Example Mystery Archive (2004-2010)",
        (_parse(),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "ambiguous-top-candidates" in result.evidence.reasons


def test_parenthesized_root_year_can_break_same_title_tie() -> None:
    original = ProviderShow(_identity("original"), "Example Mystery", 2004)
    remake = ProviderShow(_identity("remake"), "Example Mystery", 2014)
    provider = _provider(original, remake)

    result = resolve_show_group_with_provider(
        "Example Mystery (2004)",
        (_parse(),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == _identity("original")
    assert "structural-source-year:2004" in result.evidence.reasons


def test_root_year_does_not_promote_weak_fuzzy_title_evidence() -> None:
    old = ProviderShow(_identity("old"), "Example Academy", 2004)
    new = ProviderShow(_identity("new"), "Example Academy", 2014)
    provider = _provider(old, new)

    result = resolve_show_group_with_provider(
        "Example Academy New Collection (2004-2010)",
        (
            ParseResult(
                series_hint="Example Academy New",
                season=1,
                episodes=(1,),
            ),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "provider-evidence-below-threshold" in result.evidence.reasons
    assert provider.search_calls == ["Example Academy New"]
