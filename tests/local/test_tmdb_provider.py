from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from jellyfin_show_organizer.models import ProviderIdentity
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
