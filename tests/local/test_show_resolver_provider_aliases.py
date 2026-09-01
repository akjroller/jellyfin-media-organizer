from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import NumberingMode, ParseResult, ProviderIdentity
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.planner import PlanningConfig, execute_plan
from jellyfin_show_organizer.provider_aliases import ProviderAliasSnapshot
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
from jellyfin_show_organizer.tvmaze_alias_cache import TVMAZE_AKAS_URL
from jellyfin_show_organizer.tvmaze_cache import (
    TVMAZE_EPISODES_URL,
    TVMAZE_SEARCH_URL,
)

pytestmark = pytest.mark.local

ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


def _episode(show: str, value: str, season: int, number: int) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", f"{show}-{value}"),
        season=season,
        number=number,
        title=f"Episode {value}",
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


class AliasProvider:
    provider_name = "fixture"

    def __init__(
        self,
        shows: tuple[ProviderShow, ...],
        aliases: Mapping[ProviderIdentity, ProviderAliasSnapshot],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog] | None = None,
    ) -> None:
        self.shows = shows
        self.aliases = dict(aliases)
        self.catalogs = dict(catalogs or {})
        self.search_calls: list[str] = []
        self.alias_calls: list[ProviderIdentity] = []
        self.catalog_calls: list[ProviderIdentity] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return ProviderSearchSnapshot(
            provider=self.provider_name,
            request_key="search:fixture",
            cache_snapshot_id="search-v1",
            shows=self.shows,
        )

    def show_aliases(self, show_identity: ProviderIdentity) -> ProviderAliasSnapshot:
        self.alias_calls.append(show_identity)
        return self.aliases[show_identity]

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        return self.catalogs[show_identity]


def _aliases(
    identity: ProviderIdentity,
    *values: str,
    errors: tuple[str, ...] = (),
    unresolved_reason: str | None = None,
) -> ProviderAliasSnapshot:
    return ProviderAliasSnapshot(
        provider="fixture",
        request_key=f"akas:{identity.value}",
        cache_snapshot_id=f"akas-{identity.value}-v1",
        show_identity=identity,
        aliases=() if unresolved_reason is not None else tuple(values),
        errors=() if unresolved_reason is not None else errors,
        unresolved_reason=unresolved_reason,
    )


def test_primary_title_match_does_not_require_alias_provider_data() -> None:
    provider = AliasProvider(
        shows=(ProviderShow(ALPHA, "Example Series", None),),
        aliases={ALPHA: _aliases(ALPHA, "Unused Alternate")},
    )

    result = resolve_show_group_with_provider(
        "Example Series",
        (ParseResult(series_hint="Example Series"),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert provider.alias_calls == []
    assert provider.catalog_calls == []


def test_exact_romanized_provider_alias_is_first_class_evidence() -> None:
    provider = AliasProvider(
        shows=(ProviderShow(ALPHA, "Example: The Translated Title", None),),
        aliases={ALPHA: _aliases(ALPHA, "Example Romanized Title")},
    )

    result = resolve_show_group_with_provider(
        "Example Romanized Title",
        (ParseResult(series_hint="Example Romanized Title"),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert result.show.title == "Example: The Translated Title"
    assert result.evidence.method == "fixture-search+provider-aliases"
    candidate = result.evidence.candidates[0]
    assert candidate.score == 0.9
    assert (
        "exact-normalized-provider-alias:example romanized title" in candidate.reasons
    )
    assert provider.alias_calls == [ALPHA]


def test_alias_collision_does_not_guess_between_candidates() -> None:
    provider = AliasProvider(
        shows=(
            ProviderShow(ALPHA, "Translated Alpha", None),
            ProviderShow(BETA, "Translated Beta", None),
        ),
        aliases={
            ALPHA: _aliases(ALPHA, "Example Romanized Title"),
            BETA: _aliases(BETA, "Example Romanized Title"),
        },
    )

    result = resolve_show_group_with_provider(
        "Example Romanized Title",
        (ParseResult(series_hint="Example Romanized Title"),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert [candidate.score for candidate in result.evidence.candidates] == [0.9, 0.9]
    assert provider.catalog_calls == []


def test_misleading_near_match_alias_cannot_match_by_itself() -> None:
    provider = AliasProvider(
        shows=(ProviderShow(ALPHA, "Unrelated Primary Title", None),),
        aliases={ALPHA: _aliases(ALPHA, "Example Romanized Titles")},
    )

    result = resolve_show_group_with_provider(
        "Example Romanized Title",
        (ParseResult(series_hint="Example Romanized Title"),),
        load_overrides(),
        provider,
    )

    assert result.status is not ResolutionStatus.MATCHED
    assert result.show is None
    assert result.evidence.candidates[0].score < 0.9
    assert any(
        reason.startswith("provider-alias-similarity:")
        for reason in result.evidence.candidates[0].reasons
    )


def test_low_confidence_text_can_be_rescued_by_one_unique_group_catalog() -> None:
    provider = AliasProvider(
        shows=(
            ProviderShow(ALPHA, "Completely Different Alpha", None),
            ProviderShow(BETA, "Another Different Beta", None),
        ),
        aliases={ALPHA: _aliases(ALPHA), BETA: _aliases(BETA)},
        catalogs={
            ALPHA: _catalog(ALPHA, (_episode("alpha", "one", 1, 1),)),
            BETA: _catalog(
                BETA,
                (
                    _episode("beta", "one", 1, 1),
                    _episode("beta", "target", 8, 12),
                ),
            ),
        },
    )

    result = resolve_show_group_with_provider(
        "Short Alternate Name",
        (
            ParseResult(
                series_hint="Short Alternate Name",
                season=1,
                episodes=(1,),
            ),
            ParseResult(
                series_hint="Short Alternate Name",
                season=8,
                episodes=(12,),
            ),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == BETA
    assert result.show.numbering_mode is NumberingMode.AIRED
    assert result.evidence.method.endswith("+catalog-rescue")
    assert "catalog-rescue:unique-compatible-candidate" in result.evidence.reasons
    assert "catalog-rescue-winner:fixture:beta" in result.evidence.reasons
    assert set(provider.catalog_calls) == {ALPHA, BETA}


def test_catalog_rescue_remains_ambiguous_when_multiple_candidates_fit() -> None:
    shared = (
        _episode("shared", "one", 1, 1),
        _episode("shared", "target", 8, 12),
    )
    provider = AliasProvider(
        shows=(
            ProviderShow(ALPHA, "Different Alpha", None),
            ProviderShow(BETA, "Different Beta", None),
        ),
        aliases={ALPHA: _aliases(ALPHA), BETA: _aliases(BETA)},
        catalogs={
            ALPHA: _catalog(ALPHA, shared),
            BETA: _catalog(BETA, shared),
        },
    )

    result = resolve_show_group_with_provider(
        "Short Alternate Name",
        (
            ParseResult(
                series_hint="Short Alternate Name",
                season=1,
                episodes=(1,),
            ),
            ParseResult(
                series_hint="Short Alternate Name",
                season=8,
                episodes=(12,),
            ),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is not ResolutionStatus.MATCHED
    assert result.show is None
    assert "catalog-rescue:no-unique-compatible-candidate" in result.evidence.reasons


def test_alias_provider_failure_blocks_automatic_catalog_rescue() -> None:
    provider = AliasProvider(
        shows=(ProviderShow(ALPHA, "Different Primary", None),),
        aliases={
            ALPHA: _aliases(
                ALPHA,
                unresolved_reason="fixture-alias-provider-failure",
            )
        },
        catalogs={
            ALPHA: _catalog(ALPHA, (_episode("alpha", "target", 8, 12),)),
        },
    )

    result = resolve_show_group_with_provider(
        "Alternate Name",
        (
            ParseResult(
                series_hint="Alternate Name",
                season=8,
                episodes=(12,),
            ),
        ),
        load_overrides(),
        provider,
    )

    assert result.status is not ResolutionStatus.MATCHED
    assert result.show is None
    assert "provider-alias-evidence:indeterminate" in result.evidence.reasons
    assert provider.catalog_calls == []


class TvmazeAliasGetter:
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
                {
                    "show": {
                        "id": 4242,
                        "name": "Example Translated Title",
                        "premiered": "2024-01-01",
                    }
                }
            ]
        if url == TVMAZE_AKAS_URL.format(tvmaze_id=4242):
            return [{"name": "Example Romanized Title", "country": None}]
        if url == TVMAZE_EPISODES_URL.format(tvmaze_id=4242):
            return [
                {
                    "id": 1001,
                    "season": 1,
                    "number": 1,
                    "name": "Pilot",
                    "type": "regular",
                }
            ]
        raise AssertionError(f"unexpected provider request: {url}")


def test_tvmaze_alias_resolution_replays_from_warm_cache_with_same_plan_hash(
    tmp_path: Path,
) -> None:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    series = shows / "Example Romanized Title"
    series.mkdir(parents=True)
    destination.mkdir()
    (series / "Example Romanized Title S01E01.mkv").write_bytes(b"fabricated-video")

    cold_getter = TvmazeAliasGetter()
    first = execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit-first",
            cache_dir=tmp_path / "cache",
        ),
        cold_getter,
    )

    warm_calls: list[str] = []

    def reject_network(
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        del params
        warm_calls.append(url)
        raise AssertionError("warm alias replay attempted provider HTTP")

    second = execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit-second",
            cache_dir=tmp_path / "cache",
            offline=True,
        ),
        reject_network,
    )

    assert first.plan.records[0].show is not None
    assert first.plan.records[0].show.title == "Example Translated Title"
    assert first.preflight.plan_hash == second.preflight.plan_hash
    assert first.plan == second.plan
    assert len(cold_getter.calls) == 3
    assert warm_calls == []
