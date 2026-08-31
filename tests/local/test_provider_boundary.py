from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import ProviderIdentity
from jellyfin_show_organizer.providers import TvmazeProviderAdapter
from jellyfin_show_organizer.tvmaze_cache import (
    TVMAZE_EPISODES_URL,
    TVMAZE_SEARCH_URL,
    TvmazeCatalogCache,
)

pytestmark = pytest.mark.local


class RecordingGetter:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        return self.responses[url]


def test_provider_identity_is_namespaced_and_normalized() -> None:
    identity = ProviderIdentity("Fixture-Provider", "show-17")

    assert identity.provider == "fixture-provider"
    assert identity.value == "show-17"
    assert identity.key == "fixture-provider:show-17"


def test_tvmaze_identity_requires_canonical_positive_integer() -> None:
    assert ProviderIdentity.tvmaze(42).require_positive_int("tvmaze") == 42
    with pytest.raises(ValueError, match="canonical positive integer"):
        ProviderIdentity("tvmaze", "0042").require_positive_int("tvmaze")


def test_tvmaze_adapter_normalizes_search_and_episode_catalog(tmp_path: Path) -> None:
    getter = RecordingGetter(
        {
            TVMAZE_SEARCH_URL: [
                {
                    "score": 1.0,
                    "show": {
                        "id": 101,
                        "name": "Fixture Harbor",
                        "premiered": "2026-03-04",
                    },
                }
            ],
            TVMAZE_EPISODES_URL.format(tvmaze_id=101): [
                {
                    "id": 1001,
                    "season": 0,
                    "number": 1,
                    "name": "OVA One",
                    "airdate": "2026-05-06",
                    "type": "special",
                }
            ],
        }
    )
    adapter = TvmazeProviderAdapter(TvmazeCatalogCache(tmp_path / "cache"), getter)

    search = adapter.search_shows("Fixture Harbor")
    catalog = adapter.episode_catalog(search.shows[0].identity)

    assert search.provider == "tvmaze"
    assert search.shows[0].identity == ProviderIdentity("tvmaze", "101")
    assert search.shows[0].year == 2026
    assert search.snapshot_identity.startswith("tvmaze:search:fixture harbor:")
    assert catalog.show_identity == ProviderIdentity("tvmaze", "101")
    episode = catalog.episodes[0]
    assert episode.identity == ProviderIdentity("tvmaze", "1001")
    assert episode.tvmaze_episode_id == 1001
    assert episode.airdate == "2026-05-06"
    assert episode.episode_type == "special"
    assert catalog.snapshot_identity.startswith("tvmaze:episodes:101:")
    assert [call[0] for call in getter.calls] == [
        TVMAZE_SEARCH_URL,
        TVMAZE_EPISODES_URL.format(tvmaze_id=101),
    ]


def test_foreign_identity_fails_before_tvmaze_network_access(tmp_path: Path) -> None:
    getter = RecordingGetter({})
    adapter = TvmazeProviderAdapter(TvmazeCatalogCache(tmp_path / "cache"), getter)

    with pytest.raises(ValueError, match="expected 'tvmaze'"):
        adapter.episode_catalog(ProviderIdentity("fixture", "show-17"))

    assert getter.calls == []


def test_duplicate_provider_coordinates_are_normalized_as_catalog_errors(
    tmp_path: Path,
) -> None:
    getter = RecordingGetter(
        {
            TVMAZE_EPISODES_URL.format(tvmaze_id=101): [
                {"id": 1001, "season": 1, "number": 1, "name": "One"},
                {"id": 1002, "season": 1, "number": 1, "name": "Other One"},
            ]
        }
    )
    adapter = TvmazeProviderAdapter(TvmazeCatalogCache(tmp_path / "cache"), getter)

    catalog = adapter.episode_catalog(ProviderIdentity.tvmaze(101))

    assert "duplicate-aired-coordinate:S01E01" in catalog.errors
