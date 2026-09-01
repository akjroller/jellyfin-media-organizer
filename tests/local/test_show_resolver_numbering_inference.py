from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import NumberingMode, ParseResult, ProviderIdentity
from jellyfin_show_organizer.overrides import OverrideCatalog, ShowOverride, load_overrides
from jellyfin_show_organizer.planner import PlanningConfig, execute_plan
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
from jellyfin_show_organizer.tvmaze_cache import (
    TVMAZE_EPISODES_URL,
    TVMAZE_SEARCH_URL,
)

pytestmark = pytest.mark.local

SHOW_ID = ProviderIdentity("fixture", "series")


def _episode(value: str, season: int, number: int) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", value),
        season=season,
        number=number,
        title=f"Episode {value}",
    )


def _catalog(
    episodes: tuple[ProviderEpisode, ...],
    *,
    errors: tuple[str, ...] = (),
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key="episodes:series",
        cache_snapshot_id="catalog-v1",
        show_identity=SHOW_ID,
        episodes=episodes,
        errors=errors,
    )


class StaticProvider:
    provider_name = "fixture"

    def __init__(self, catalog: ProviderEpisodeCatalog) -> None:
        self.catalog = catalog
        self.search_calls: list[str] = []
        self.catalog_calls: list[ProviderIdentity] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return ProviderSearchSnapshot(
            provider=self.provider_name,
            request_key="search:example-series",
            cache_snapshot_id="search-v1",
            shows=(ProviderShow(SHOW_ID, "Example Series", 2024),),
        )

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        assert show_identity == SHOW_ID
        return self.catalog


def _resolve(
    parses: tuple[ParseResult, ...],
    catalog: ProviderEpisodeCatalog,
    overrides: OverrideCatalog | None = None,
):
    provider = StaticProvider(catalog)
    result = resolve_show_group_with_provider(
        "Example Series",
        parses,
        overrides or load_overrides(),
        provider,
    )
    return result, provider


def _three_episode_catalog() -> ProviderEpisodeCatalog:
    return _catalog(
        (
            _episode("one", 1, 1),
            _episode("two", 1, 2),
            _episode("three", 2, 1),
        )
    )


def test_unique_aired_compatibility_selects_aired_mode() -> None:
    result, provider = _resolve(
        (
            ParseResult(
                series_hint="Example Series",
                year=2024,
                season=2,
                episodes=(1,),
                absolute_episode=9,
            ),
        ),
        _three_episode_catalog(),
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.numbering_mode is NumberingMode.AIRED
    assert "numbering-candidate:aired" in result.evidence.reasons
    assert "numbering-candidate:absolute" in result.evidence.reasons
    assert "numbering-compatible:true:aired" in result.evidence.reasons
    assert "numbering-compatible:false:absolute" in result.evidence.reasons
    assert "numbering-selected:aired" in result.evidence.reasons
    assert provider.catalog_calls == [SHOW_ID]


def test_unique_absolute_compatibility_selects_absolute_mode() -> None:
    result, _provider = _resolve(
        (
            ParseResult(
                series_hint="Example Series",
                year=2024,
                season=9,
                episodes=(9,),
                absolute_episode=3,
            ),
        ),
        _three_episode_catalog(),
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.numbering_mode is NumberingMode.ABSOLUTE
    assert "numbering-compatible:false:aired" in result.evidence.reasons
    assert "numbering-compatible:true:absolute" in result.evidence.reasons
    assert "numbering-map:absolute:3->S02E01:fixture:three" in result.evidence.reasons
    assert "numbering-selected:absolute" in result.evidence.reasons


def test_both_compatible_modes_remain_suspicious() -> None:
    result, _provider = _resolve(
        (
            ParseResult(
                series_hint="Example Series",
                year=2024,
                season=2,
                episodes=(1,),
                absolute_episode=3,
            ),
        ),
        _three_episode_catalog(),
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "numbering-compatible:true:aired" in result.evidence.reasons
    assert "numbering-compatible:true:absolute" in result.evidence.reasons
    assert "numbering-inference:no-unique-compatible-mode" in result.evidence.reasons


def test_neither_compatible_mode_remains_suspicious() -> None:
    result, _provider = _resolve(
        (
            ParseResult(
                series_hint="Example Series",
                year=2024,
                season=8,
                episodes=(12,),
                absolute_episode=12,
            ),
        ),
        _three_episode_catalog(),
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "numbering-compatible:false:aired" in result.evidence.reasons
    assert "numbering-compatible:false:absolute" in result.evidence.reasons
    assert "numbering-inference:no-unique-compatible-mode" in result.evidence.reasons


def test_partial_catalog_cannot_eliminate_a_numbering_mode() -> None:
    result, _provider = _resolve(
        (
            ParseResult(
                series_hint="Example Series",
                year=2024,
                season=9,
                episodes=(9,),
                absolute_episode=3,
            ),
        ),
        _catalog(
            (
                _episode("one", 1, 1),
                _episode("two", 1, 2),
                _episode("three", 2, 1),
            ),
            errors=("invalid-catalog-season:3",),
        ),
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "numbering-inference:indeterminate-catalog" in result.evidence.reasons
    assert "numbering-catalog-error:invalid-catalog-season:3" in result.evidence.reasons


def test_mixed_single_mode_files_do_not_produce_a_group_guess() -> None:
    result, _provider = _resolve(
        (
            ParseResult(
                series_hint="Example Series",
                year=2024,
                season=1,
                episodes=(1,),
            ),
            ParseResult(
                series_hint="Example Series",
                year=2024,
                absolute_episode=2,
            ),
        ),
        _three_episode_catalog(),
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "numbering-inference:mixed-or-incomplete-group-evidence"
        in result.evidence.reasons
    )


def test_show_override_numbering_mode_remains_higher_authority() -> None:
    overrides = OverrideCatalog(
        schema_version=1,
        shows=(
            ShowOverride(
                key="Example Series",
                provider_identity=SHOW_ID,
                numbering_mode=NumberingMode.ABSOLUTE,
            ),
        ),
    )
    result, provider = _resolve(
        (
            ParseResult(
                series_hint="Example Series",
                year=2024,
                season=2,
                episodes=(1,),
                absolute_episode=3,
            ),
        ),
        _three_episode_catalog(),
        overrides,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.numbering_mode is NumberingMode.ABSOLUTE
    assert "numbering-selected:override:absolute" in result.evidence.reasons
    assert provider.search_calls == []
    assert provider.catalog_calls == []


class TvmazeGetter:
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
                        "name": "Example Series",
                        "premiered": "2024-01-01",
                    }
                }
            ]
        if url == TVMAZE_EPISODES_URL.format(tvmaze_id=4242):
            return [
                {"id": 1001, "season": 1, "number": 1, "name": "One"},
                {"id": 1002, "season": 1, "number": 2, "name": "Two"},
                {"id": 1003, "season": 2, "number": 1, "name": "Three"},
            ]
        raise AssertionError(f"unexpected provider request: {url}")


def test_absolute_inference_plan_replays_offline_with_same_hash(tmp_path: Path) -> None:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    series = shows / "Example Series"
    series.mkdir(parents=True)
    destination.mkdir()
    source = series / "Example Series - 3.mkv"
    source.write_bytes(b"fabricated-video")

    getter = TvmazeGetter()
    first = execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit-first",
            cache_dir=tmp_path / "cache",
        ),
        getter,
    )

    warm_calls: list[str] = []

    def reject_network(
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        del params
        warm_calls.append(url)
        raise AssertionError("offline numbering replay attempted provider HTTP")

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
    assert first.plan.records[0].show.numbering_mode is NumberingMode.ABSOLUTE
    assert first.preflight.plan_hash == second.preflight.plan_hash
    assert first.plan == second.plan
    assert len(getter.calls) == 2
    assert warm_calls == []
