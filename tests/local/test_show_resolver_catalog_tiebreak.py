from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import ParseResult
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.show_resolver import ResolutionStatus, resolve_show_group
from jellyfin_show_organizer.tvmaze_cache import (
    TVMAZE_EPISODES_URL,
    TVMAZE_SEARCH_URL,
    TvmazeCatalogCache,
)

pytestmark = pytest.mark.local


SEARCH_RESPONSE = [
    {"show": {"id": 1001, "name": "Example Series", "premiered": None}},
    {"show": {"id": 2002, "name": "Example Series", "premiered": None}},
]
PARSES = (
    ParseResult(series_hint="Example Series", season=1, episodes=(1,)),
    ParseResult(series_hint="Example Series", season=8, episodes=(12,)),
)


def _episode(episode_id: int, season: int, number: int, name: str) -> dict[str, object]:
    return {
        "id": episode_id,
        "season": season,
        "number": number,
        "name": name,
    }


def _complete_catalog(base_id: int) -> list[dict[str, object]]:
    return [
        _episode(base_id + 1, 1, 1, "First"),
        _episode(base_id + 2, 8, 12, "Twelfth"),
    ]


class RoutedGetter:
    def __init__(self, catalogs: Mapping[int, object]) -> None:
        self.catalogs = dict(catalogs)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, dict(params or {})))
        if url == TVMAZE_SEARCH_URL:
            return SEARCH_RESPONSE
        for tvmaze_id, response in self.catalogs.items():
            if url == TVMAZE_EPISODES_URL.format(tvmaze_id=tvmaze_id):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected provider URL: {url}")


class NoNetworkGetter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append(url)
        raise AssertionError("offline replay must not call the provider")


def test_unique_catalog_compatibility_breaks_exact_title_tie(tmp_path: Path) -> None:
    getter = RoutedGetter(
        {
            1001: [_episode(100101, 1, 1, "First")],
            2002: _complete_catalog(200200),
        }
    )

    resolution = resolve_show_group(
        "example-series",
        PARSES,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.tvmaze_id == 2002
    assert resolution.evidence.method == "tvmaze-search+episode-catalog"
    assert "catalog-tiebreak:unique-aired-coordinate-match" in (
        resolution.evidence.reasons
    )
    assert "catalog-winner:tvmaze:2002" in resolution.evidence.reasons
    loser = next(
        candidate
        for candidate in resolution.evidence.candidates
        if candidate.tvmaze_id == 1001
    )
    winner = next(
        candidate
        for candidate in resolution.evidence.candidates
        if candidate.tvmaze_id == 2002
    )
    assert "catalog-missing:S08E12" in loser.reasons
    assert "catalog-aired-match:2/2" in winner.reasons
    assert "catalog-tiebreak-winner" in winner.reasons


def test_equal_catalog_compatibility_remains_suspicious(tmp_path: Path) -> None:
    resolution = resolve_show_group(
        "example-series",
        PARSES,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        RoutedGetter(
            {
                1001: _complete_catalog(100100),
                2002: _complete_catalog(200200),
            }
        ),
    )

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None
    assert "catalog-tiebreak:no-unique-aired-coordinate-match" in (
        resolution.evidence.reasons
    )


def test_empty_or_malformed_candidate_catalog_remains_suspicious(tmp_path: Path) -> None:
    malformed = [
        {
            "id": 100101,
            "season": 1,
            "number": 1,
        }
    ]
    for first_catalog in ([], malformed):
        cache_dir = tmp_path / f"cache-{len(first_catalog)}"
        resolution = resolve_show_group(
            "example-series",
            PARSES,
            load_overrides(),
            TvmazeCatalogCache(cache_dir),
            RoutedGetter(
                {
                    1001: first_catalog,
                    2002: _complete_catalog(200200),
                }
            ),
        )

        assert resolution.status is ResolutionStatus.SUSPICIOUS
        assert resolution.show is None
        assert "catalog-tiebreak:incomplete-candidate-catalogs" in (
            resolution.evidence.reasons
        )


def test_provider_failure_during_catalog_tiebreak_remains_suspicious(
    tmp_path: Path,
) -> None:
    resolution = resolve_show_group(
        "example-series",
        PARSES,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        RoutedGetter(
            {
                1001: TimeoutError("fabricated timeout"),
                2002: _complete_catalog(200200),
            }
        ),
    )

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None
    assert "catalog-tiebreak:incomplete-candidate-catalogs" in (
        resolution.evidence.reasons
    )
    assert any(
        reason.startswith("catalog-unresolved:tvmaze:1001:timeout:")
        for reason in resolution.evidence.reasons
    )


def test_conflicting_catalog_coordinates_do_not_create_a_winner(tmp_path: Path) -> None:
    resolution = resolve_show_group(
        "example-series",
        PARSES,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        RoutedGetter(
            {
                1001: [_episode(100101, 1, 1, "First")],
                2002: [_episode(200201, 1, 1, "First")],
            }
        ),
    )

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None
    assert "catalog-tiebreak:no-unique-aired-coordinate-match" in (
        resolution.evidence.reasons
    )


def test_catalog_tiebreak_replays_from_warm_cache_without_http(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cold = RoutedGetter(
        {
            1001: [_episode(100101, 1, 1, "First")],
            2002: _complete_catalog(200200),
        }
    )
    first = resolve_show_group(
        "example-series",
        PARSES,
        load_overrides(),
        TvmazeCatalogCache(cache_dir),
        cold,
    )

    offline = NoNetworkGetter()
    second = resolve_show_group(
        "example-series",
        PARSES,
        load_overrides(),
        TvmazeCatalogCache(cache_dir, offline=True),
        offline,
    )

    assert first == second
    assert len(cold.calls) == 3
    assert offline.calls == []
