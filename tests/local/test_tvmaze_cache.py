from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jellyfin_show_organizer import tvmaze_cache as tvmaze

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


def _clock_later() -> datetime:
    return datetime(2026, 1, 10, 0, 0, tzinfo=UTC)


def test_normalized_search_titles_share_one_cold_cache_request(tmp_path: Path):
    getter = FakeGetter()
    cache = tvmaze.TvmazeCatalogCache(tmp_path / "cache", clock=_clock)

    first = cache.search_show("  EXAMPLE SERIES  ", getter)
    second = cache.search_show("Example Series", getter)

    assert first.state is tvmaze.CacheState.OK
    assert first.source is tvmaze.CacheSource.NETWORK
    assert first.freshness is tvmaze.CacheFreshness.FRESH
    assert second.state is tvmaze.CacheState.OK
    assert second.source is tvmaze.CacheSource.CACHE
    assert second.freshness is tvmaze.CacheFreshness.FRESH
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


def test_warmed_cache_replays_with_zero_http_calls_and_same_snapshot(tmp_path: Path):
    cache_root = tmp_path / "cache"
    cold_getter = FakeGetter()
    cold = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    expected_search = cold.search_show("Example Series", cold_getter)
    expected_episodes = cold.episode_catalog(TVMAZE_ID, cold_getter)

    warm_getter = FakeGetter()
    warm = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    search = warm.search_show("EXAMPLE SERIES", warm_getter)
    episodes = warm.episode_catalog(TVMAZE_ID, warm_getter)

    assert warm_getter.calls == []
    assert search.source is tvmaze.CacheSource.CACHE
    assert episodes.source is tvmaze.CacheSource.CACHE
    assert search.response == expected_search.response
    assert episodes.response == expected_episodes.response
    assert search.snapshot_id == expected_search.snapshot_id
    assert episodes.snapshot_id == expected_episodes.snapshot_id


def test_cache_records_request_provenance_and_schema_version(tmp_path: Path):
    cache_root = tmp_path / "cache"
    cache = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    record = cache.search_show("Example Series", FakeGetter())

    search_file = next((cache_root / "search").glob("*.json"))
    payload = json.loads(search_file.read_text(encoding="utf-8"))

    assert payload["schema_version"] == tvmaze.CACHE_SCHEMA_VERSION == 2
    assert payload["provider"] == "tvmaze"
    assert payload["request_key"] == "search:example series"
    assert payload["request_url"] == tvmaze.TVMAZE_SEARCH_URL
    assert payload["request_params"] == {"q": "example series"}
    assert payload["retrieved_at"] == "2026-01-01T00:00:00Z"
    assert len(record.snapshot_id) == 64


def test_search_and_episode_freshness_use_separate_policy_windows(tmp_path: Path):
    cache_root = tmp_path / "cache"
    policy = tvmaze.CachePolicy(
        search_max_age=timedelta(days=1),
        episodes_max_age=timedelta(days=30),
    )
    cold = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock, policy=policy)
    cold.search_show("Example Series", FakeGetter())
    cold.episode_catalog(TVMAZE_ID, FakeGetter())

    getter = FakeGetter()
    warm = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock_later, policy=policy)
    search = warm.search_show("Example Series", getter)
    episodes = warm.episode_catalog(TVMAZE_ID, getter)

    assert getter.calls == []
    assert search.freshness is tvmaze.CacheFreshness.STALE
    assert episodes.freshness is tvmaze.CacheFreshness.FRESH


def test_stale_cache_does_not_refresh_without_deliberate_refresh_mode(tmp_path: Path):
    cache_root = tmp_path / "cache"
    policy = tvmaze.CachePolicy(search_max_age=timedelta(days=1))
    cold = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock, policy=policy)
    expected = cold.search_show("Example Series", FakeGetter())

    getter = FakeGetter()
    getter.search_response = [{"show": {"id": 99999}}]
    ordinary = tvmaze.TvmazeCatalogCache(
        cache_root,
        clock=_clock_later,
        policy=policy,
    )
    record = ordinary.search_show("Example Series", getter)

    assert getter.calls == []
    assert record.freshness is tvmaze.CacheFreshness.STALE
    assert record.response == expected.response
    assert record.snapshot_id == expected.snapshot_id


def test_refresh_mode_replaces_stale_entry_and_offline_replays_it(tmp_path: Path):
    cache_root = tmp_path / "cache"
    policy = tvmaze.CachePolicy(search_max_age=timedelta(days=1))
    cold = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock, policy=policy)
    cold.search_show("Example Series", FakeGetter())

    refresh_getter = FakeGetter()
    refresh_getter.search_response = [{"show": {"id": 99999}}]
    refresh = tvmaze.TvmazeCatalogCache(
        cache_root,
        clock=_clock_later,
        policy=policy,
        refresh=True,
    )
    refreshed = refresh.search_show("Example Series", refresh_getter)

    assert len(refresh_getter.calls) == 1
    assert refreshed.source is tvmaze.CacheSource.NETWORK
    assert refreshed.freshness is tvmaze.CacheFreshness.FRESH
    assert refreshed.response == refresh_getter.search_response

    offline_getter = FakeGetter()
    replay = tvmaze.TvmazeCatalogCache(
        cache_root,
        clock=_clock_later,
        policy=policy,
        offline=True,
    ).search_show("Example Series", offline_getter)
    assert offline_getter.calls == []
    assert replay.response == refreshed.response
    assert replay.snapshot_id == refreshed.snapshot_id


def test_offline_cold_miss_performs_zero_http_and_writes_nothing(tmp_path: Path):
    cache_root = tmp_path / "cache"
    getter = FakeGetter()
    cache = tvmaze.TvmazeCatalogCache(cache_root, offline=True)

    record = cache.search_show("Example Series", getter)

    assert getter.calls == []
    assert record.state is tvmaze.CacheState.MISS
    assert record.source is tvmaze.CacheSource.POLICY
    assert record.unresolved_reason == "offline cache miss"
    assert not cache_root.exists()


def test_corrupt_cache_entry_is_explicit_and_offline_never_refetches(tmp_path: Path):
    cache_root = tmp_path / "cache"
    cold = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    cold.search_show("Example Series", FakeGetter())
    search_file = next((cache_root / "search").glob("*.json"))
    search_file.write_text("{not-json", encoding="utf-8")

    getter = FakeGetter()
    record = tvmaze.TvmazeCatalogCache(cache_root, offline=True).search_show(
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
    assert first.failure_kind is tvmaze.ProviderFailureKind.UNKNOWN
    assert first.unresolved_reason is not None
    assert "RuntimeError" in first.unresolved_reason
    assert second.state is tvmaze.CacheState.ERROR
    assert second.source is tvmaze.CacheSource.CACHE
    assert second.failure_kind is first.failure_kind
    assert second.unresolved_reason == first.unresolved_reason


class HttpError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (TimeoutError("timed out"), tvmaze.ProviderFailureKind.TIMEOUT),
        (HttpError(429), tvmaze.ProviderFailureKind.RATE_LIMIT),
        (HttpError(404), tvmaze.ProviderFailureKind.NOT_FOUND),
        (HttpError(503), tvmaze.ProviderFailureKind.TRANSIENT_HTTP),
        (ConnectionError("connection failed"), tvmaze.ProviderFailureKind.NETWORK),
    ],
)
def test_provider_failures_are_classified_explicitly(
    tmp_path: Path,
    error: Exception,
    expected_kind: tvmaze.ProviderFailureKind,
):
    def failing_getter(
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        raise error

    record = tvmaze.TvmazeCatalogCache(tmp_path / "cache").episode_catalog(
        TVMAZE_ID,
        failing_getter,
    )

    assert record.state is tvmaze.CacheState.ERROR
    assert record.failure_kind is expected_kind
    assert record.unresolved_reason is not None
    assert record.unresolved_reason.startswith(expected_kind.value)


def test_malformed_provider_response_fails_closed(tmp_path: Path):
    getter = FakeGetter()
    getter.search_response = {"unexpected": "object"}

    record = tvmaze.TvmazeCatalogCache(tmp_path / "cache").search_show(
        "Example Series",
        getter,
    )

    assert record.state is tvmaze.CacheState.ERROR
    assert record.failure_kind is tvmaze.ProviderFailureKind.MALFORMED_RESPONSE
    assert record.response is None
    assert record.unresolved_reason is not None
    assert "malformed provider response" in record.unresolved_reason


def test_cache_writes_are_atomic_and_leave_no_temp_files(tmp_path: Path):
    cache_root = tmp_path / "cache"
    cache = tvmaze.TvmazeCatalogCache(cache_root, clock=_clock)
    cache.search_show("Example Series", FakeGetter())
    cache.episode_catalog(TVMAZE_ID, FakeGetter())

    cache_files = sorted(cache_root.rglob("*.json"))
    assert len(cache_files) == 2
    assert not list(cache_root.rglob("*.tmp"))


def test_offline_and_refresh_modes_are_mutually_exclusive(tmp_path: Path):
    with pytest.raises(ValueError, match="offline and refresh"):
        tvmaze.TvmazeCatalogCache(tmp_path / "cache", offline=True, refresh=True)
