"""Small, disk-backed TMDb cache used by the optional provider adapter.

The cache deliberately stores provider responses without credentials.  The
caller supplies an authenticated getter (normally :func:`tmdb_http_getter`)
and the cache only persists URL, non-secret parameters, and normalized cache
state.  It shares the TVMaze cache's offline, refresh, freshness, and failure
policy through :class:`~.tvmaze_cache.ProviderCatalogCache`.
"""

from __future__ import annotations

import hashlib
from typing import cast

from .provider_http import TMDB_MIN_INTERVAL_SECONDS, ThrottledJsonGetter
from .tvmaze_cache import (
    CacheKind,
    CacheRecord,
    JsonGetter,
    MalformedProviderResponse,
    ProviderCatalogCache,
)

TMDB_PROVIDER = "tmdb"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/tv"
TMDB_SHOW_URL = "https://api.themoviedb.org/3/tv/{tmdb_id}"
TMDB_SEASON_URL = "https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}"


def tmdb_http_getter(access_token: str) -> ThrottledJsonGetter:
    """Return an authenticated getter without placing the token in cache files."""

    token = access_token.strip()
    if not token:
        raise ValueError("TMDb access token cannot be empty")
    return ThrottledJsonGetter(
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "JellyfinMediaOrganizer/optional-tmdb",
        },
        min_interval=TMDB_MIN_INTERVAL_SECONDS,
    )


def _key(prefix: str, value: str) -> str:
    return f"{prefix}:{value.strip().casefold()}"


def _filename(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"


def _validate_object(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("provider response must be a JSON object")


def _validate_episode_list(value: object) -> None:
    if not isinstance(value, list):
        raise ValueError("TMDb episode catalog must be an array")


class TmdbCatalogCache(ProviderCatalogCache):
    """Cache TMDb search and season responses under a provider-specific root."""

    provider_name = TMDB_PROVIDER

    def search_show(self, title: str, getter: JsonGetter) -> CacheRecord:
        request_key = _key("search", title)
        params = {"query": title.strip()}
        return self._get_or_fetch(
            path=self.root / "search" / _filename(request_key),
            kind=CacheKind.SEARCH,
            request_key=request_key,
            url=TMDB_SEARCH_URL,
            params=params,
            fetch=lambda: getter(TMDB_SEARCH_URL, params),
            validator=_validate_object,
        )

    def episode_catalog(self, tmdb_id: int, getter: JsonGetter) -> CacheRecord:
        if tmdb_id <= 0:
            raise ValueError("tmdb_id must be positive")
        return self._get_or_fetch(
            path=self.root / "episodes" / f"{tmdb_id}.json",
            kind=CacheKind.EPISODES,
            request_key=f"episodes:{tmdb_id}",
            url=TMDB_SHOW_URL.format(tmdb_id=tmdb_id),
            params={},
            fetch=lambda: _fetch_episodes(tmdb_id, getter),
            validator=_validate_episode_list,
        )


def _fetch_episodes(tmdb_id: int, getter: JsonGetter) -> list[object]:
    """Flatten every season of one TMDb show into one episode list."""

    show = getter(TMDB_SHOW_URL.format(tmdb_id=tmdb_id), None)
    if not isinstance(show, dict):
        raise MalformedProviderResponse("TMDb show response must be a JSON object")
    seasons = cast(dict[str, object], show).get("seasons")
    if not isinstance(seasons, list):
        raise MalformedProviderResponse("TMDb show response seasons must be an array")
    episodes: list[object] = []
    for season in seasons:
        if not isinstance(season, dict):
            continue
        season_number = cast(dict[str, object], season).get("season_number")
        if not isinstance(season_number, int):
            continue
        raw = getter(
            TMDB_SEASON_URL.format(tmdb_id=tmdb_id, season=season_number),
            None,
        )
        if not isinstance(raw, dict):
            raise MalformedProviderResponse(
                "TMDb season response must be a JSON object"
            )
        season_episodes = cast(dict[str, object], raw).get("episodes")
        if isinstance(season_episodes, list):
            episodes.extend(season_episodes)
    return episodes
