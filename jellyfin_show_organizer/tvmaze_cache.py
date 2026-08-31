from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

CACHE_SCHEMA_VERSION = 2
TVMAZE_SEARCH_URL = "https://api.tvmaze.com/search/shows"
TVMAZE_EPISODES_URL = "https://api.tvmaze.com/shows/{tvmaze_id}/episodes"
TVMAZE_PROVIDER = "tvmaze"


class CacheKind(StrEnum):
    SEARCH = "search"
    EPISODES = "episodes"


class CacheState(StrEnum):
    OK = "ok"
    ERROR = "error"
    CORRUPT = "corrupt"
    MISS = "miss"


class CacheSource(StrEnum):
    CACHE = "cache"
    NETWORK = "network"
    POLICY = "policy"


class CacheFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class ProviderFailureKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate-limit"
    NOT_FOUND = "not-found"
    TRANSIENT_HTTP = "transient-http"
    NETWORK = "network"
    MALFORMED_RESPONSE = "malformed-response"
    UNKNOWN = "unknown"


class JsonGetter(Protocol):
    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class CachePolicy:
    search_max_age: timedelta = timedelta(days=7)
    episodes_max_age: timedelta = timedelta(days=30)

    def __post_init__(self) -> None:
        if self.search_max_age <= timedelta(0):
            raise ValueError("search_max_age must be positive")
        if self.episodes_max_age <= timedelta(0):
            raise ValueError("episodes_max_age must be positive")

    def max_age_for(self, kind: CacheKind) -> timedelta:
        if kind is CacheKind.SEARCH:
            return self.search_max_age
        return self.episodes_max_age


@dataclass(frozen=True, slots=True)
class CacheRecord:
    kind: CacheKind
    request_key: str
    retrieved_at: str | None
    state: CacheState
    response: object | None
    error: str | None
    source: CacheSource
    provider: str = TVMAZE_PROVIDER
    request_url: str | None = None
    request_params: tuple[tuple[str, str], ...] = ()
    failure_kind: ProviderFailureKind | None = None
    freshness: CacheFreshness = CacheFreshness.UNKNOWN

    @property
    def resolved(self) -> bool:
        return self.state is CacheState.OK

    @property
    def unresolved_reason(self) -> str | None:
        if self.resolved:
            return None
        return self.error or self.state.value

    @property
    def snapshot_id(self) -> str:
        payload = json.dumps(
            _encode_record(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


Clock = Callable[[], datetime]
Validator = Callable[[object], None]


def normalize_search_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title)
    return " ".join(normalized.casefold().split())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(clock: Clock) -> str:
    current = clock()
    if current.tzinfo is None:
        raise ValueError("cache clock must return a timezone-aware datetime")
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("cache retrieved_at must include a timezone")
    return parsed.astimezone(UTC)


def _search_key(title: str) -> str:
    return f"search:{normalize_search_title(title)}"


def _episodes_key(tvmaze_id: int) -> str:
    if tvmaze_id <= 0:
        raise ValueError("tvmaze_id must be positive")
    return f"episodes:{tvmaze_id}"


def _search_filename(request_key: str) -> str:
    digest = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
    return f"{digest}.json"


def _request_params(params: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(params.items()))


def _validate_list_response(response: object) -> None:
    if not isinstance(response, list):
        raise ValueError("provider response must be a JSON array")
    if any(not isinstance(item, dict) for item in response):
        raise ValueError("provider response array items must be JSON objects")


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _classify_failure(error: Exception) -> ProviderFailureKind:
    if isinstance(error, TimeoutError):
        return ProviderFailureKind.TIMEOUT
    status = _status_code(error)
    if status == 429:
        return ProviderFailureKind.RATE_LIMIT
    if status == 404:
        return ProviderFailureKind.NOT_FOUND
    if status is not None and 500 <= status <= 599:
        return ProviderFailureKind.TRANSIENT_HTTP
    if isinstance(error, ConnectionError):
        return ProviderFailureKind.NETWORK
    return ProviderFailureKind.UNKNOWN


class TvmazeCatalogCache:
    def __init__(
        self,
        root: Path,
        *,
        clock: Clock = _utc_now,
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
        normalized_title = normalize_search_title(title)
        request_key = _search_key(title)
        path = self.root / "search" / _search_filename(request_key)
        params = {"q": normalized_title}
        return self._get_or_fetch(
            path=path,
            kind=CacheKind.SEARCH,
            request_key=request_key,
            url=TVMAZE_SEARCH_URL,
            params=params,
            getter=getter,
            validator=_validate_list_response,
        )

    def episode_catalog(self, tvmaze_id: int, getter: JsonGetter) -> CacheRecord:
        request_key = _episodes_key(tvmaze_id)
        path = self.root / "episodes" / f"{tvmaze_id}.json"
        params = {"specials": "1"}
        return self._get_or_fetch(
            path=path,
            kind=CacheKind.EPISODES,
            request_key=request_key,
            url=TVMAZE_EPISODES_URL.format(tvmaze_id=tvmaze_id),
            params=params,
            getter=getter,
            validator=_validate_list_response,
        )

    def _get_or_fetch(
        self,
        *,
        path: Path,
        kind: CacheKind,
        request_key: str,
        url: str,
        params: Mapping[str, str],
        getter: JsonGetter,
        validator: Validator,
    ) -> CacheRecord:
        cached = self._read(path, kind, request_key)
        if cached is not None:
            cached = self._with_freshness(cached)
            if self.offline:
                return cached
            if not self.refresh:
                return cached
            if cached.state is CacheState.OK and cached.freshness is CacheFreshness.FRESH:
                return cached

        if self.offline:
            return self._miss_record(kind, request_key, url, params)

        record = self._fetch(kind, request_key, url, params, getter, validator)
        self._write(path, record)
        return record

    def _fetch(
        self,
        kind: CacheKind,
        request_key: str,
        url: str,
        params: Mapping[str, str],
        getter: JsonGetter,
        validator: Validator,
    ) -> CacheRecord:
        try:
            response = getter(url, params)
        except Exception as exc:
            return self._error_record(kind, request_key, url, params, exc)

        try:
            validator(response)
        except (TypeError, ValueError) as exc:
            return CacheRecord(
                kind=kind,
                request_key=request_key,
                retrieved_at=_timestamp(self._clock),
                state=CacheState.ERROR,
                response=None,
                error=f"malformed provider response: {exc}",
                source=CacheSource.NETWORK,
                request_url=url,
                request_params=_request_params(params),
                failure_kind=ProviderFailureKind.MALFORMED_RESPONSE,
                freshness=CacheFreshness.UNKNOWN,
            )

        return self._ok_record(kind, request_key, url, params, response)

    def _ok_record(
        self,
        kind: CacheKind,
        request_key: str,
        url: str,
        params: Mapping[str, str],
        response: object,
    ) -> CacheRecord:
        return CacheRecord(
            kind=kind,
            request_key=request_key,
            retrieved_at=_timestamp(self._clock),
            state=CacheState.OK,
            response=response,
            error=None,
            source=CacheSource.NETWORK,
            request_url=url,
            request_params=_request_params(params),
            freshness=CacheFreshness.FRESH,
        )

    def _error_record(
        self,
        kind: CacheKind,
        request_key: str,
        url: str,
        params: Mapping[str, str],
        error: Exception,
    ) -> CacheRecord:
        failure_kind = _classify_failure(error)
        return CacheRecord(
            kind=kind,
            request_key=request_key,
            retrieved_at=_timestamp(self._clock),
            state=CacheState.ERROR,
            response=None,
            error=f"{failure_kind.value}: {type(error).__name__}: {error}",
            source=CacheSource.NETWORK,
            request_url=url,
            request_params=_request_params(params),
            failure_kind=failure_kind,
            freshness=CacheFreshness.UNKNOWN,
        )

    def _miss_record(
        self,
        kind: CacheKind,
        request_key: str,
        url: str,
        params: Mapping[str, str],
    ) -> CacheRecord:
        return CacheRecord(
            kind=kind,
            request_key=request_key,
            retrieved_at=None,
            state=CacheState.MISS,
            response=None,
            error="offline cache miss",
            source=CacheSource.POLICY,
            request_url=url,
            request_params=_request_params(params),
            freshness=CacheFreshness.UNKNOWN,
        )

    def _with_freshness(self, record: CacheRecord) -> CacheRecord:
        if record.state is not CacheState.OK or record.retrieved_at is None:
            return replace(record, freshness=CacheFreshness.UNKNOWN)
        retrieved = _parse_timestamp(record.retrieved_at)
        current = self._clock()
        if current.tzinfo is None:
            raise ValueError("cache clock must return a timezone-aware datetime")
        age = current.astimezone(UTC) - retrieved
        freshness = (
            CacheFreshness.FRESH
            if age <= self.policy.max_age_for(record.kind)
            else CacheFreshness.STALE
        )
        return replace(record, freshness=freshness)

    def _read(
        self,
        path: Path,
        kind: CacheKind,
        request_key: str,
    ) -> CacheRecord | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            record = _decode_record(raw)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
        ) as exc:
            return CacheRecord(
                kind=kind,
                request_key=request_key,
                retrieved_at=None,
                state=CacheState.CORRUPT,
                response=None,
                error=f"corrupt cache entry: {type(exc).__name__}: {exc}",
                source=CacheSource.CACHE,
            )

        if record.kind is not kind or record.request_key != request_key:
            return CacheRecord(
                kind=kind,
                request_key=request_key,
                retrieved_at=record.retrieved_at,
                state=CacheState.CORRUPT,
                response=None,
                error="corrupt cache entry: request identity mismatch",
                source=CacheSource.CACHE,
            )
        return replace(record, source=CacheSource.CACHE)

    def _write(self, path: Path, record: CacheRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            _encode_record(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8", newline="\n")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _encode_record(record: CacheRecord) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "provider": record.provider,
        "kind": record.kind.value,
        "request_key": record.request_key,
        "request_url": record.request_url,
        "request_params": dict(record.request_params),
        "retrieved_at": record.retrieved_at,
        "state": record.state.value,
        "response": record.response,
        "error": record.error,
        "failure_kind": (
            record.failure_kind.value if record.failure_kind is not None else None
        ),
    }


def _decode_record(raw: object) -> CacheRecord:
    if not isinstance(raw, dict):
        raise ValueError("cache entry must be an object")
    value = cast(dict[str, object], raw)
    required = {
        "schema_version",
        "provider",
        "kind",
        "request_key",
        "request_url",
        "request_params",
        "retrieved_at",
        "state",
        "response",
        "error",
        "failure_kind",
    }
    if set(value) != required:
        raise ValueError("cache entry has unexpected fields")
    if value["schema_version"] != CACHE_SCHEMA_VERSION:
        raise ValueError("unsupported cache schema version")

    provider = value["provider"]
    request_key = value["request_key"]
    request_url = value["request_url"]
    request_params = value["request_params"]
    retrieved_at = value["retrieved_at"]
    error = value["error"]
    failure_kind_value = value["failure_kind"]
    kind_value = value["kind"]
    state_value = value["state"]
    if provider != TVMAZE_PROVIDER:
        raise ValueError("cache provider is invalid")
    if not isinstance(request_key, str) or not request_key:
        raise ValueError("cache request_key must be a non-empty string")
    if request_url is not None and not isinstance(request_url, str):
        raise ValueError("cache request_url must be a string or null")
    if not isinstance(request_params, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in request_params.items()
    ):
        raise ValueError("cache request_params must map strings to strings")
    if retrieved_at is not None:
        if not isinstance(retrieved_at, str):
            raise ValueError("cache retrieved_at must be a string or null")
        _parse_timestamp(retrieved_at)
    if error is not None and not isinstance(error, str):
        raise ValueError("cache error must be a string or null")
    if not isinstance(kind_value, str) or not isinstance(state_value, str):
        raise ValueError("cache kind/state must be strings")
    if failure_kind_value is not None and not isinstance(failure_kind_value, str):
        raise ValueError("cache failure_kind must be a string or null")

    try:
        kind = CacheKind(kind_value)
        state = CacheState(state_value)
        failure_kind = (
            ProviderFailureKind(failure_kind_value)
            if failure_kind_value is not None
            else None
        )
    except ValueError as exc:
        raise ValueError("cache enum value is invalid") from exc

    if state is CacheState.OK and error is not None:
        raise ValueError("successful cache entries cannot contain an error")
    if state is CacheState.ERROR and not error:
        raise ValueError("error cache entries require an error message")
    if state is CacheState.MISS:
        raise ValueError("offline misses must not be persisted")

    return CacheRecord(
        kind=kind,
        request_key=request_key,
        retrieved_at=retrieved_at,
        state=state,
        response=value["response"],
        error=error,
        source=CacheSource.CACHE,
        provider=provider,
        request_url=request_url,
        request_params=_request_params(cast(dict[str, str], request_params)),
        failure_kind=failure_kind,
        freshness=CacheFreshness.UNKNOWN,
    )
