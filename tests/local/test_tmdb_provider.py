from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from jellyfin_show_organizer.models import ProviderIdentity
from jellyfin_show_organizer.provider_http import ProviderHttpError
from jellyfin_show_organizer.providers import (
    AutoProviderAdapter,
    ProviderSearchSnapshot,
    ProviderShow,
    TmdbProviderAdapter,
)
from jellyfin_show_organizer.tmdb_cache import (
    TMDB_SEARCH_URL,
    TMDB_SEASON_URL,
    TMDB_SHOW_URL,
    TmdbCatalogCache,
    tmdb_http_getter,
)
from jellyfin_show_organizer.tvmaze_alias_cache import TvmazeAliasCache
from jellyfin_show_organizer.tvmaze_cache import (
    CacheFreshness,
    CacheSource,
    CacheState,
    ProviderFailureKind,
    TvmazeCatalogCache,
)


def test_tmdb_adapter_normalizes_search_and_seasons(tmp_path: Path) -> None:
    def getter(url: str, _params: object = None) -> object:
        if url == TMDB_SEARCH_URL:
            return {
                "results": [
                    {"id": 100, "name": "Example Show", "first_air_date": "2020-01-02"}
                ]
            }
        if url == TMDB_SHOW_URL.format(tmdb_id=100):
            return {"seasons": [{"season_number": 1}, {"season_number": 0}]}
        if url == TMDB_SEASON_URL.format(tmdb_id=100, season=1):
            return {
                "episodes": [
                    {
                        "id": 1001,
                        "season_number": 1,
                        "episode_number": 1,
                        "name": "Pilot",
                        "air_date": "2020-01-02",
                        "type": "standard",
                    }
                ]
            }
        if url == TMDB_SEASON_URL.format(tmdb_id=100, season=0):
            return {"episodes": []}
        raise AssertionError(url)

    adapter = TmdbProviderAdapter(
        TmdbCatalogCache(tmp_path / "cache"), cast(Any, getter)
    )
    search = adapter.search_shows("Example Show")
    assert search.provider == "tmdb"
    assert search.shows[0].identity == ProviderIdentity("tmdb", "100")
    assert search.shows[0].year == 2020

    catalog = adapter.episode_catalog(ProviderIdentity("tmdb", "100"))
    assert catalog.resolved
    assert catalog.episodes[0].identity == ProviderIdentity("tmdb", "1001")
    assert catalog.episodes[0].season == 1
    assert catalog.episodes[0].number == 1


def test_tmdb_cache_replays_offline_without_getter(tmp_path: Path) -> None:
    calls: list[str] = []

    def getter(url: str, _params: object = None) -> object:
        calls.append(url)
        if url == TMDB_SEARCH_URL:
            return {"results": []}
        raise AssertionError(url)

    online = TmdbCatalogCache(tmp_path / "cache")
    assert online.search_show("Nothing", cast(Any, getter)).resolved
    offline = TmdbCatalogCache(tmp_path / "cache", offline=True)
    assert offline.search_show(
        "Nothing", lambda *_: (_ for _ in ()).throw(AssertionError())
    ).resolved
    assert len(calls) == 1


def test_tmdb_cache_corruption_is_not_replayed_offline(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    online = TmdbCatalogCache(cache_root)
    online.search_show("Example", cast(Any, lambda *_: {"results": []}))
    cached_file = next((cache_root / "search").glob("*.json"))
    cached_file.write_text("{not-json", encoding="utf-8")

    offline = TmdbCatalogCache(cache_root, offline=True)
    record = offline.search_show(
        "Example",
        cast(Any, lambda *_: (_ for _ in ()).throw(AssertionError())),
    )

    assert record.state is CacheState.CORRUPT
    assert not record.resolved


def test_tmdb_http_getter_rejects_missing_token() -> None:
    try:
        tmdb_http_getter(" ")
    except ValueError as exc:
        assert "access token" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing TMDb token should be rejected")


def test_auto_provider_does_not_call_tmdb_for_a_unique_tvmaze_match() -> None:
    class Primary:
        provider_name = "tvmaze"

        def search_shows(self, _title: str) -> ProviderSearchSnapshot:
            return ProviderSearchSnapshot(
                provider="tvmaze",
                request_key="search:example",
                cache_snapshot_id="tvmaze-snapshot",
                shows=(ProviderShow(ProviderIdentity("tvmaze", "1"), "Example", 2020),),
            )

        def episode_catalog(self, _identity: ProviderIdentity):  # pragma: no cover
            raise AssertionError

    class Secondary:
        def search_shows(self, _title: str):  # pragma: no cover
            raise AssertionError("TMDb should not be consulted")

    provider = AutoProviderAdapter(cast(Any, Primary()), cast(Any, Secondary()))
    result = provider.search_shows("Example")
    assert result.shows[0].identity == ProviderIdentity("tvmaze", "1")
    try:
        provider.episode_catalog(ProviderIdentity("tvmaze", "1"))
    except AssertionError:
        pass
    else:  # pragma: no cover - the primary stub must own episode lookup
        raise AssertionError("episode lookup did not delegate to TVMaze")


def test_auto_provider_delegates_primary_alias_evidence() -> None:
    expected = object()

    class Primary:
        provider_name = "tvmaze"

        def show_aliases(self, _identity: ProviderIdentity) -> object:
            return expected

        def search_shows(self, _title: str):  # pragma: no cover
            raise AssertionError

        def episode_catalog(self, _identity: ProviderIdentity):  # pragma: no cover
            raise AssertionError

    provider = AutoProviderAdapter(cast(Any, Primary()), None)
    assert provider.show_aliases(ProviderIdentity("tvmaze", "1")) is expected


def test_auto_provider_recovers_one_tvmaze_identity_from_tmdb_title() -> None:
    show = ProviderShow(ProviderIdentity("tvmaze", "1"), "Example", 2020)

    class Primary:
        provider_name = "tvmaze"

        def __init__(self) -> None:
            self.calls = 0

        def search_shows(self, title: str) -> ProviderSearchSnapshot:
            self.calls += 1
            if self.calls == 1:
                return ProviderSearchSnapshot(
                    provider="tvmaze",
                    request_key="search:example",
                    cache_snapshot_id="miss",
                    shows=(),
                    unresolved_reason="offline cache miss",
                )
            return ProviderSearchSnapshot(
                provider="tvmaze",
                request_key="search:example",
                cache_snapshot_id="retry",
                shows=(show,),
            )

    class Secondary:
        def search_shows(self, _title: str) -> ProviderSearchSnapshot:
            return ProviderSearchSnapshot(
                provider="tmdb",
                request_key="search:example",
                cache_snapshot_id="tmdb",
                shows=(ProviderShow(ProviderIdentity("tmdb", "2"), "Example", 2020),),
            )

    result = AutoProviderAdapter(
        cast(Any, Primary()), cast(Any, Secondary())
    ).search_shows("Example")
    assert result.shows == (show,)


def test_auto_provider_preserves_ambiguous_provider_disagreement() -> None:
    candidates = (
        ProviderShow(ProviderIdentity("tvmaze", "1"), "Alpha", 2020),
        ProviderShow(ProviderIdentity("tvmaze", "3"), "Gamma", 2020),
    )

    class Primary:
        provider_name = "tvmaze"

        def search_shows(self, _title: str) -> ProviderSearchSnapshot:
            return ProviderSearchSnapshot(
                provider="tvmaze",
                request_key="search:example",
                cache_snapshot_id="tvmaze",
                shows=candidates,
            )

    class Secondary:
        def search_shows(self, _title: str) -> ProviderSearchSnapshot:
            return ProviderSearchSnapshot(
                provider="tmdb",
                request_key="search:example",
                cache_snapshot_id="tmdb",
                shows=(ProviderShow(ProviderIdentity("tmdb", "2"), "Beta", 2020),),
            )

    result = AutoProviderAdapter(
        cast(Any, Primary()), cast(Any, Secondary())
    ).search_shows("Example")

    assert result.resolved
    assert result.shows == candidates
    assert len(result.shows) != 1


def test_auto_provider_requires_consensus_for_ambiguous_tvmaze_results() -> None:
    candidates = (
        ProviderShow(ProviderIdentity("tvmaze", "1"), "Example", 2020),
        ProviderShow(ProviderIdentity("tvmaze", "2"), "Example", 2021),
    )

    class Primary:
        provider_name = "tvmaze"

        def search_shows(self, _title: str) -> ProviderSearchSnapshot:
            return ProviderSearchSnapshot(
                provider="tvmaze",
                request_key="search:example",
                cache_snapshot_id="tvmaze",
                shows=candidates,
            )

    class Secondary:
        def search_shows(self, _title: str) -> ProviderSearchSnapshot:
            return ProviderSearchSnapshot(
                provider="tmdb",
                request_key="search:example",
                cache_snapshot_id="tmdb",
                shows=(ProviderShow(ProviderIdentity("tmdb", "3"), "Example", 2020),),
            )

    result = AutoProviderAdapter(
        cast(Any, Primary()), cast(Any, Secondary())
    ).search_shows("Example")
    assert result.shows == candidates[:]


def test_tmdb_refresh_does_not_refetch_a_fresh_entry(tmp_path: Path) -> None:
    calls: list[str] = []

    def getter(url: str, _params: object = None) -> object:
        calls.append(url)
        return {"results": []}

    TmdbCatalogCache(tmp_path / "cache").search_show("Example", cast(Any, getter))
    record = TmdbCatalogCache(tmp_path / "cache", refresh=True).search_show(
        "Example", cast(Any, getter)
    )

    assert len(calls) == 1
    assert record.source is CacheSource.CACHE
    assert record.freshness is CacheFreshness.FRESH


def test_tmdb_failures_are_classified_and_transient_ones_not_persisted(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"

    def rate_limited(url: str, _params: object = None) -> object:
        raise ProviderHttpError(429, "provider HTTP 429")

    limited = TmdbCatalogCache(cache_root).search_show(
        "Example", cast(Any, rate_limited)
    )
    assert limited.failure_kind is ProviderFailureKind.RATE_LIMIT
    assert not (cache_root / "search").exists()

    def missing(url: str, _params: object = None) -> object:
        raise ProviderHttpError(404, "provider HTTP 404")

    not_found = TmdbCatalogCache(cache_root).episode_catalog(77, cast(Any, missing))
    assert not_found.failure_kind is ProviderFailureKind.NOT_FOUND
    replay = TmdbCatalogCache(cache_root, offline=True).episode_catalog(
        77, cast(Any, missing)
    )
    assert replay.source is CacheSource.CACHE
    assert replay.failure_kind is ProviderFailureKind.NOT_FOUND


def test_tmdb_malformed_show_is_a_malformed_response(tmp_path: Path) -> None:
    def getter(url: str, _params: object = None) -> object:
        return {"seasons": "not-a-list"}

    record = TmdbCatalogCache(tmp_path / "cache").episode_catalog(77, cast(Any, getter))

    assert record.state is CacheState.ERROR
    assert record.failure_kind is ProviderFailureKind.MALFORMED_RESPONSE


def test_tmdb_failed_refresh_keeps_good_episode_catalog(tmp_path: Path) -> None:
    def good(url: str, _params: object = None) -> object:
        if url == TMDB_SHOW_URL.format(tmdb_id=77):
            return {"seasons": [{"season_number": 1}]}
        return {"episodes": [{"id": 1, "season_number": 1, "episode_number": 1}]}

    first = TmdbCatalogCache(tmp_path / "cache").episode_catalog(77, cast(Any, good))

    def failing(url: str, _params: object = None) -> object:
        raise TimeoutError("timed out")

    refreshed = TmdbCatalogCache(tmp_path / "cache", refresh=True).episode_catalog(
        77, cast(Any, failing)
    )

    assert first.resolved
    assert refreshed.resolved
    assert refreshed.snapshot_id == first.snapshot_id


def test_alias_cache_retries_transient_failures_but_keeps_not_found(
    tmp_path: Path,
) -> None:
    aliases = TvmazeAliasCache(TvmazeCatalogCache(tmp_path / "cache"))
    calls: list[str] = []

    def flaky(url: str, _params: object = None) -> object:
        calls.append(url)
        raise ConnectionError("connection reset")

    assert not aliases.show_aliases(5, cast(Any, flaky)).resolved
    assert not aliases.show_aliases(5, cast(Any, flaky)).resolved
    assert len(calls) == 2

    def missing(url: str, _params: object = None) -> object:
        calls.append(url)
        raise ProviderHttpError(404, "provider HTTP 404")

    aliases.show_aliases(6, cast(Any, missing))
    replay = aliases.show_aliases(6, cast(Any, missing))
    assert len(calls) == 3
    assert replay.source == "cache"
