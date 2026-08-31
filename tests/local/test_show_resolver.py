from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import NumberingMode, ParseResult
from jellyfin_show_organizer.overrides import OverrideCatalog, load_overrides
from jellyfin_show_organizer.show_resolver import (
    ResolutionStatus,
    normalize_show_identity,
    resolve_show_group,
)
from jellyfin_show_organizer.tvmaze_cache import TVMAZE_SEARCH_URL, TvmazeCatalogCache

pytestmark = pytest.mark.local


class FakeGetter:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, dict(params or {})))
        assert url == TVMAZE_SEARCH_URL
        return self.response


def _search_response() -> list[dict[str, object]]:
    return [
        {
            "score": 1.0,
            "show": {
                "id": 101,
                "name": "Mirror City",
                "premiered": "2005-01-01",
            },
        },
        {
            "score": 1.0,
            "show": {
                "id": 202,
                "name": "Mirror City",
                "premiered": "2024-02-02",
            },
        },
    ]


def _catalog(tmp_path: Path, text: str) -> OverrideCatalog:
    path = tmp_path / "overrides.toml"
    path.write_text(text.strip(), encoding="utf-8")
    return load_overrides(path)


def test_year_disambiguates_two_identically_titled_editions(tmp_path: Path):
    getter = FakeGetter(_search_response())
    resolution = resolve_show_group(
        "mirror-city-2005",
        (ParseResult(series_hint="Mirror City", year=2005),),
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.tvmaze_id == 101
    assert resolution.show.year == 2005
    assert resolution.evidence.candidates[0].score == 1.0


def test_missing_year_keeps_identical_editions_suspicious(tmp_path: Path):
    resolution = resolve_show_group(
        "mirror-city",
        (ParseResult(series_hint="Mirror City"),),
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        FakeGetter(_search_response()),
    )

    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None
    assert len(resolution.evidence.candidates) == 2
    assert resolution.evidence.candidates[0].score == 0.9
    assert resolution.evidence.candidates[1].score == 0.9
    assert "ambiguous-top-candidates" in resolution.evidence.reasons


def test_explicit_override_alias_resolves_without_network(tmp_path: Path):
    catalog = _catalog(
        tmp_path,
        """
schema_version = 1

[[shows]]
key = "clockwork-isles"
tvmaze_id = 303
aliases = ["Clockwork Isles", "Isles of Clockwork"]
year = 2022
numbering_mode = "absolute"
title_preference = "override"
preferred_title = "The Clockwork Isles"
""",
    )
    getter = FakeGetter([])

    resolution = resolve_show_group(
        "clockwork-isles",
        (ParseResult(series_hint="Isles of Clockwork", year=2022),),
        catalog,
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert getter.calls == []
    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.tvmaze_id == 303
    assert resolution.show.title == "The Clockwork Isles"
    assert resolution.show.numbering_mode is NumberingMode.ABSOLUTE


def test_conflicting_explicit_ids_fail_closed_without_network(tmp_path: Path):
    getter = FakeGetter([])

    resolution = resolve_show_group(
        "northstar-files",
        (
            ParseResult(series_hint="Northstar Files", embedded_tvmaze_id=111),
            ParseResult(series_hint="Northstar Files", embedded_tvmaze_id=222),
        ),
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert getter.calls == []
    assert resolution.status is ResolutionStatus.UNRESOLVED
    assert resolution.show is None
    assert "conflicting-explicit-tvmaze-ids" in resolution.evidence.reasons


def test_conflicting_source_years_fail_closed_without_network(tmp_path: Path):
    getter = FakeGetter([])

    resolution = resolve_show_group(
        "mirror-city",
        (
            ParseResult(series_hint="Mirror City", year=2005),
            ParseResult(series_hint="Mirror City", year=2024),
        ),
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert getter.calls == []
    assert resolution.status is ResolutionStatus.UNRESOLVED
    assert "conflicting-source-years" in resolution.evidence.reasons


def test_weak_provider_candidate_remains_unresolved(tmp_path: Path):
    getter = FakeGetter(
        [
            {
                "score": 0.2,
                "show": {
                    "id": 909,
                    "name": "Completely Different Program",
                    "premiered": "2010-01-01",
                },
            }
        ]
    )

    resolution = resolve_show_group(
        "river-patrol",
        (ParseResult(series_hint="River Patrol"),),
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert resolution.status is ResolutionStatus.UNRESOLVED
    assert resolution.show is None
    assert resolution.evidence.candidates
    assert resolution.evidence.candidates[0].score < 0.75


def test_warmed_group_resolution_replays_without_http(tmp_path: Path):
    cache = TvmazeCatalogCache(tmp_path / "cache")
    cold_getter = FakeGetter(_search_response())
    parses = (ParseResult(series_hint="Mirror City", year=2005),)

    first = resolve_show_group(
        "mirror-city-2005",
        parses,
        load_overrides(),
        cache,
        cold_getter,
    )
    warm_getter = FakeGetter([])
    second = resolve_show_group(
        "mirror-city-2005",
        parses,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        warm_getter,
    )

    assert first == second
    assert len(cold_getter.calls) == 1
    assert warm_getter.calls == []


def test_identity_normalization_is_punctuation_and_case_insensitive():
    assert normalize_show_identity("  Mirror.City: Refracted  ") == (
        normalize_show_identity("mirror city - refracted")
    )
