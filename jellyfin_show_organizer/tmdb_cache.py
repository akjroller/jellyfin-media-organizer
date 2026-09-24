"""Small, disk-backed TMDb cache used by the optional provider adapter.

The cache deliberately stores provider responses without credentials.  The
caller supplies an authenticated getter (normally :func:`tmdb_http_getter`)
and the cache only persists URL, non-secret parameters, and normalized cache
state.  It follows the same offline/refresh semantics as the TVMaze cache.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from .tvmaze_cache import (
    CacheFreshness,
    CacheKind,
    CachePolicy,
    CacheRecord,
    CacheSource,
    CacheState,
    Clock,
    JsonGetter,
    ProviderFailureKind,
)

TMDB_PROVIDER = "tmdb"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/tv"
TMDB_SHOW_URL = "https://api.themoviedb.org/3/tv/{tmdb_id}"
TMDB_SEASON_URL = "https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}"


def tmdb_http_getter(access_token: str):
    """Return an authenticated getter without placing the token in cache files."""

    token = access_token.strip()
    if not token:
        raise ValueError("TMDb access token cannot be empty")

    def getter(url: str, params: Mapping[str, str] | None = None) -> object:
        query = urlencode(sorted((params or {}).items()))
        target = f"{url}?{query}" if query else url
        request = Request(
            target,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "JellyfinMediaOrganizer/optional-tmdb",
            },
        )
        with urlopen(request, timeout=20) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    return getter


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(clock: Clock) -> str:
    current = clock()
    if current.tzinfo is None:
        raise ValueError("cache clock must return a timezone-aware datetime")
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _key(prefix: str, value: str) -> str:
    return f"{prefix}:{value.strip().casefold()}"


def _filename(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"


def _validate_object(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("provider response must be a JSON object")


class TmdbCatalogCache:
    """Cache TMDb search and season responses under a provider-specific root."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Clock = _now,
        policy: CachePolicy | None = None,
        offline: bool = False,
        refresh: bool = False,
    ) -> None:
        if offline and refresh:
            raise ValueError("offline and refresh modes cannot be enabled together")
        self.root = root
        self._clock = clock
        self.policy = policy or CachePolicy()
        self.offline = offline
        self.refresh = refresh

    def search_show(self, title: str, getter: JsonGetter) -> CacheRecord:
        request_key = _key("search", title)
        return self._get_or_fetch(
            self.root / "search" / _filename(request_key),
            CacheKind.SEARCH,
            request_key,
            TMDB_SEARCH_URL,
            {"query": title.strip()},
            getter,
            _validate_object,
        )

    def episode_catalog(self, tmdb_id: int, getter: JsonGetter) -> CacheRecord:
        if tmdb_id <= 0:
            raise ValueError("tmdb_id must be positive")
        request_key = f"episodes:{tmdb_id}"
        path = self.root / "episodes" / f"{tmdb_id}.json"
        cached = self._read(path, CacheKind.EPISODES, request_key)
        if cached is not None and (self.offline or not self.refresh):
            return cached
        if self.offline:
            return CacheRecord(
                kind=CacheKind.EPISODES,
                request_key=request_key,
                retrieved_at=None,
                state=CacheState.MISS,
                response=None,
                error="offline cache miss",
                source=CacheSource.POLICY,
                provider=TMDB_PROVIDER,
                request_url=TMDB_SHOW_URL.format(tmdb_id=tmdb_id),
            )
        try:
            show = getter(TMDB_SHOW_URL.format(tmdb_id=tmdb_id), None)
            _validate_object(show)
            show_mapping = cast(dict[str, object], show)
            seasons = show_mapping.get("seasons")
            if not isinstance(seasons, list):
                raise ValueError("TMDb show response seasons must be an array")
            episodes: list[object] = []
            for season in seasons:
                if not isinstance(season, dict) or not isinstance(
                    cast(dict[str, object], season).get("season_number"), int
                ):
                    continue
                season_number = cast(
                    int, cast(dict[str, object], season)["season_number"]
                )
                raw = getter(
                    TMDB_SEASON_URL.format(tmdb_id=tmdb_id, season=season_number),
                    None,
                )
                _validate_object(raw)
                season_episodes = cast(dict[str, object], raw).get("episodes")
                if isinstance(season_episodes, list):
                    episodes.extend(season_episodes)
            record = CacheRecord(
                kind=CacheKind.EPISODES,
                request_key=request_key,
                retrieved_at=_timestamp(self._clock),
                state=CacheState.OK,
                response=episodes,
                error=None,
                source=CacheSource.NETWORK,
                provider=TMDB_PROVIDER,
                request_url=TMDB_SHOW_URL.format(tmdb_id=tmdb_id),
                freshness=CacheFreshness.FRESH,
            )
        except Exception as exc:
            record = CacheRecord(
                kind=CacheKind.EPISODES,
                request_key=request_key,
                retrieved_at=_timestamp(self._clock),
                state=CacheState.ERROR,
                response=None,
                error=f"{ProviderFailureKind.UNKNOWN.value}: {type(exc).__name__}: {exc}",
                source=CacheSource.NETWORK,
                provider=TMDB_PROVIDER,
                request_url=TMDB_SHOW_URL.format(tmdb_id=tmdb_id),
                failure_kind=ProviderFailureKind.UNKNOWN,
            )
        self._write(path, record)
        return record

    def _get_or_fetch(
        self,
        path: Path,
        kind: CacheKind,
        request_key: str,
        url: str,
        params: Mapping[str, str],
        getter: JsonGetter,
        validator: Any,
    ) -> CacheRecord:
        cached = self._read(path, kind, request_key)
        if cached is not None and (self.offline or not self.refresh):
            return cached
        if self.offline:
            return CacheRecord(
                kind=kind,
                request_key=request_key,
                retrieved_at=None,
                state=CacheState.MISS,
                response=None,
                error="offline cache miss",
                source=CacheSource.POLICY,
                provider=TMDB_PROVIDER,
                request_url=url,
                request_params=tuple(sorted(params.items())),
            )
        try:
            response = getter(url, params)
            validator(response)
            record = CacheRecord(
                kind=kind,
                request_key=request_key,
                retrieved_at=_timestamp(self._clock),
                state=CacheState.OK,
                response=response,
                error=None,
                source=CacheSource.NETWORK,
                provider=TMDB_PROVIDER,
                request_url=url,
                request_params=tuple(sorted(params.items())),
                freshness=CacheFreshness.FRESH,
            )
        except Exception as exc:
            record = CacheRecord(
                kind=kind,
                request_key=request_key,
                retrieved_at=_timestamp(self._clock),
                state=CacheState.ERROR,
                response=None,
                error=f"{ProviderFailureKind.UNKNOWN.value}: {type(exc).__name__}: {exc}",
                source=CacheSource.NETWORK,
                provider=TMDB_PROVIDER,
                request_url=url,
                request_params=tuple(sorted(params.items())),
                failure_kind=ProviderFailureKind.UNKNOWN,
            )
        self._write(path, record)
        return record

    def _read(
        self, path: Path, kind: CacheKind, request_key: str
    ) -> CacheRecord | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("provider") != TMDB_PROVIDER:
                raise ValueError("cache provider is invalid")
            record = CacheRecord(
                kind=CacheKind(value["kind"]),
                request_key=value["request_key"],
                retrieved_at=value.get("retrieved_at"),
                state=CacheState(value["state"]),
                response=value.get("response"),
                error=value.get("error"),
                source=CacheSource.CACHE,
                provider=value["provider"],
                request_url=value.get("request_url"),
                request_params=tuple(sorted(value.get("request_params", {}).items())),
                failure_kind=(
                    ProviderFailureKind(value["failure_kind"])
                    if value.get("failure_kind")
                    else None
                ),
            )
            if record.kind is not kind or record.request_key != request_key:
                raise ValueError("cache request identity mismatch")
            return record
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return CacheRecord(
                kind=kind,
                request_key=request_key,
                retrieved_at=None,
                state=CacheState.CORRUPT,
                response=None,
                error="corrupt cache entry",
                source=CacheSource.CACHE,
                provider=TMDB_PROVIDER,
            )

    def _write(self, path: Path, record: CacheRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 2,
            "provider": record.provider,
            "kind": record.kind.value,
            "request_key": record.request_key,
            "request_url": record.request_url,
            "request_params": dict(record.request_params),
            "retrieved_at": record.retrieved_at,
            "state": record.state.value,
            "response": record.response,
            "error": record.error,
            "failure_kind": record.failure_kind.value
            if record.failure_kind is not None
            else None,
        }
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)
