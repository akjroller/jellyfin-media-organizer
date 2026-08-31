from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import ParseResult
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.show_resolver import ResolutionStatus, resolve_show_group
from jellyfin_show_organizer.tvmaze_cache import TVMAZE_SEARCH_URL, TvmazeCatalogCache

pytestmark = pytest.mark.local


class FakeGetter:
    def __init__(self, title: str, tvmaze_id: int = 101) -> None:
        self.title = title
        self.tvmaze_id = tvmaze_id
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, dict(params or {})))
        assert url == TVMAZE_SEARCH_URL
        return [
            {
                "score": 1.0,
                "show": {
                    "id": self.tvmaze_id,
                    "name": self.title,
                    "premiered": "2024-01-01",
                },
            }
        ]


def test_majority_title_beats_alphabetically_earlier_outlier(tmp_path: Path) -> None:
    getter = FakeGetter("Majority Show")
    parses = (
        ParseResult(series_hint="Majority Show"),
        ParseResult(series_hint="Majority Show"),
        ParseResult(series_hint="Majority Show"),
        ParseResult(series_hint="Majority Show"),
        ParseResult(series_hint="Majority Show"),
        ParseResult(series_hint="Alpha Outlier"),
        ParseResult(series_hint="   "),
        ParseResult(series_hint=None),
    )

    resolution = resolve_show_group(
        "mixed-source-folder",
        parses,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.tvmaze_id == 101
    assert getter.calls == [(TVMAZE_SEARCH_URL, {"q": "majority show"})]


def test_equal_frequency_uses_normalized_title_tiebreak_independent_of_order(
    tmp_path: Path,
) -> None:
    forward = (
        ParseResult(series_hint="Beta Show"),
        ParseResult(series_hint="Alpha Show"),
        ParseResult(series_hint="Beta Show"),
        ParseResult(series_hint="Alpha Show"),
    )
    reverse = tuple(reversed(forward))
    first_getter = FakeGetter("Alpha Show", tvmaze_id=202)
    second_getter = FakeGetter("Alpha Show", tvmaze_id=202)

    first = resolve_show_group(
        "tie-source-folder",
        forward,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "first-cache"),
        first_getter,
    )
    second = resolve_show_group(
        "tie-source-folder",
        reverse,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "second-cache"),
        second_getter,
    )

    assert first == second
    assert first_getter.calls == [(TVMAZE_SEARCH_URL, {"q": "alpha show"})]
    assert second_getter.calls == [(TVMAZE_SEARCH_URL, {"q": "alpha show"})]


def test_selected_identity_uses_most_frequent_display_form(tmp_path: Path) -> None:
    getter = FakeGetter("Mirror City", tvmaze_id=303)
    parses = (
        ParseResult(series_hint="Mirror.City"),
        ParseResult(series_hint="Mirror.City"),
        ParseResult(series_hint="Mirror.City"),
        ParseResult(series_hint="mirror city"),
    )

    resolution = resolve_show_group(
        "mirror-source-folder",
        parses,
        load_overrides(),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert resolution.status is ResolutionStatus.MATCHED
    assert getter.calls == [(TVMAZE_SEARCH_URL, {"q": "mirror.city"})]
