from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jellyfin_show_organizer import tvmaze_cache as tvmaze

pytestmark = pytest.mark.local
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "tvmaze"
TVMAZE_ID = 12345


def _fixture(name: str) -> object:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class FakeGetter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.search_response = _fixture("example-search.json")
        self.episode_response = _fixture("example-episodes.json")

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        normalized_params = dict(params or {})
        self.calls.append((url, normalized_params))
        if url == tvmaze.TVMAZE_SEARCH_URL:
            return self.search_response
        if url == tvmaze.TVMAZE_EPISODES_URL.format(tvmaze_id=TVMAZE_ID):
            return self.episode_response
        raise AssertionError(f"unexpected request: {url}")


def _clock() -> datetime:
    return datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def test_normalized_search_titles_share_one_cold_cache_request(tmp_path: Path):
    getter = FakeGetter()
    cache = tvmaze.TvmazeCatalogCache(tmp_path / "cache", clock=_clock)

    first = cache.search_show("  EXAMPLE SERIES  ", getter)
    second = cache.search_show("Example Series", getter)

    assert first.state is tvmaze.CacheState.OK
    assert first.source is tvmaze.CacheSource.NETWORK
    assert second.state is tvmaze.CacheState.OK
    assert second.source is tvmaze.CacheSource.CACHE
    assert first.response == second.response == getter.search_response
    assert getter.calls == [(tvmaze.TVMAZE_SEARCH_URL, {"q": "example series"})]
    assert tvmaze.normalize_search_title("  EXAMPLE SERIES  ") == "example series"


def test_cold_show_uses_at_most_one_search_and_one_catalog_fetch(tmp_path: Path):
    getter = FakeGetter()
    cache = tvmaze.TvmazeCatalogCache(tmp_path / "cache", clock=_clock)

    cache.search_show("Example Series", getter)
    cache.search_show("example series", getter)
    cache.episode_catalog(TVMAZE_ID, getter)
    cache.episode_catalog(TVMAZE_ID, getter)

    assert getter.calls == [
        (tvmaze.TVMAZE_SEARCH_URL, {"q": "example series"}),
        (
            tvmaze.TVMAZE_EPISODES_URL.format(tvmaze_id=TVMAZE_ID),
            {"specials": "1"},
        ),
    ]


def test_warmed_cache_replays_with_zero_http_calls(tmp_path: Path):
    cache_root = tmp_path / "cache"
    cold_getter = FakeGetter()
    cold = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    expected_search = cold.search_show("Example Series", cold_getter).response
    expected_episodes = cold.episode_catalog(TVMAZE_ID, cold_getter).response

    warm_getter = FakeGetter()
    warm = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    search = warm.search_show("EXAMPLE SERIES", warm_getter)
    episodes = warm.episode_catalog(TVMAZE_ID, warm_getter)

    assert warm_getter.calls == []
    assert search.source is tvmaze.CacheSource.CACHE
    assert episodes.source is tvmaze.CacheSource.CACHE
    assert search.response == expected_search
    assert episodes.response == expected_episodes


def test_cache_writes_are_atomic_and_leave_no_temp_files(tmp_path: Path):
    cache_root = tmp_path / "cache"
    cache = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    cache.search_show("Example Series", FakeGetter())
    cache.episode_catalog(TVMAZE_ID, FakeGetter())

    cache_files = sorted(cache_root.rglob("*.json"))
    assert len(cache_files) == 2
    assert not list(cache_root.rglob("*.tmp"))
    for path in cache_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["retrieved_at"] == "2026-01-01T00:00:00Z"
        assert payload["response"] is not None


def test_corrupt_cache_entry_is_explicit_and_does_not_refetch(tmp_path: Path):
    cache_root = tmp_path / "cache"
    cold = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    cold.search_show("Example Series", FakeGetter())
    search_file = next((cache_root / "search").glob("*.json"))
    search_file.write_text("{not-json", encoding="utf-8")

    getter = FakeGetter()
    record = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock).search_show(
        "Example Series",
        getter,
    )

    assert record.state is tvmaze.CacheState.CORRUPT
    assert record.source is tvmaze.CacheSource.CACHE
    assert record.resolved is False
    assert record.unresolved_reason is not None
    assert "corrupt cache entry" in record.unresolved_reason
    assert getter.calls == []


def test_network_error_is_cached_as_explicit_unresolved_record(tmp_path: Path):
    calls = 0

    def failing_getter(
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"offline: {url} {params}")

    cache_root = tmp_path / "cache"
    cache = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    first = cache.episode_catalog(TVMAZE_ID, failing_getter)
    second = cache.episode_catalog(TVMAZE_ID, failing_getter)

    assert calls == 1
    assert first.state is tvmaze.CacheState.ERROR
    assert first.source is tvmaze.CacheSource.NETWORK
    assert first.unresolved_reason is not None
    assert "RuntimeError" in first.unresolved_reason
    assert second.state is tvmaze.CacheState.ERROR
    assert second.source is tvmaze.CacheSource.CACHE
    assert second.unresolved_reason == first.unresolved_reason
