from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

CACHE_SCHEMA_VERSION = 1
TVMAZE_SEARCH_URL = "https://api.tvmaze.com/search/shows"
TVMAZE_EPISODES_URL = "https://api.tvmaze.com/shows/{tvmaze_id}/episodes"


class CacheKind(StrEnum):
    SEARCH = "search"
    EPISODES = "episodes"


class CacheState(StrEnum):
    OK = "ok"
    ERROR = "error"
    CORRUPT = "corrupt"


class CacheSource(StrEnum):
    CACHE = "cache"
    NETWORK = "network"


class JsonGetter(Protocol):
    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class CacheRecord:
    kind: CacheKind
    request_key: str
    retrieved_at: str | None
    state: CacheState
    response: object | None
    error: str | None
    source: CacheSource

    @property
    def resolved(self) -> bool:
        return self.state is CacheState.OK

    @property
    def unresolved_reason(self) -> str | None:
        if self.resolved:
            return None
        return self.error or self.state.value


Clock = Callable[[], datetime]


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


def _search_key(title: str) -> str:
    return f"search:{normalize_search_title(title)}"


def _episodes_key(tvmaze_id: int) -> str:
    if tvmaze_id <= 0:
        raise ValueError("tvmaze_id must be positive")
    return f"episodes:{tvmaze_id}"


def _search_filename(request_key: str) -> str:
    digest = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
    return f"{digest}.json"


class TvmazeCatalogCache:
    def __init__(self, root: Path, *, clock: Clock = _utc_now) -> None:
        self.root = root
        self._clock = clock

    def search_show(self, title: str, getter: JsonGetter) -> CacheRecord:
        request_key = _search_key(title)
        path = self.root / "search" / _search_filename(request_key)
        cached = self._read(path, CacheKind.SEARCH, request_key)
        if cached is not None:
            return cached

        try:
            response = getter(
                TVMAZE_SEARCH_URL,
                {"q": normalize_search_title(title)},
            )
        except Exception as exc:
            record = self._error_record(
                CacheKind.SEARCH,
                request_key,
                exc,
            )
        else:
            record = self._ok_record(
                CacheKind.SEARCH,
                request_key,
                response,
            )
        self._write(path, record)
        return record

    def episode_catalog(self, tvmaze_id: int, getter: JsonGetter) -> CacheRecord:
        request_key = _episodes_key(tvmaze_id)
        path = self.root / "episodes" / f"{tvmaze_id}.json"
        cached = self._read(path, CacheKind.EPISODES, request_key)
        if cached is not None:
            return cached

        try:
            response = getter(
                TVMAZE_EPISODES_URL.format(tvmaze_id=tvmaze_id),
                {"specials": "1"},
            )
        except Exception as exc:
            record = self._error_record(
                CacheKind.EPISODES,
                request_key,
                exc,
            )
        else:
            record = self._ok_record(
                CacheKind.EPISODES,
                request_key,
                response,
            )
        self._write(path, record)
        return record

    def _ok_record(
        self,
        kind: CacheKind,
        request_key: str,
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
        )

    def _error_record(
        self,
        kind: CacheKind,
        request_key: str,
        error: Exception,
    ) -> CacheRecord:
        return CacheRecord(
            kind=kind,
            request_key=request_key,
            retrieved_at=_timestamp(self._clock),
            state=CacheState.ERROR,
            response=None,
            error=f"{type(error).__name__}: {error}",
            source=CacheSource.NETWORK,
        )

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
        "kind": record.kind.value,
        "request_key": record.request_key,
        "retrieved_at": record.retrieved_at,
        "state": record.state.value,
        "response": record.response,
        "error": record.error,
    }


def _decode_record(raw: object) -> CacheRecord:
    if not isinstance(raw, dict):
        raise ValueError("cache entry must be an object")
    value = cast(dict[str, object], raw)
    required = {
        "schema_version",
        "kind",
        "request_key",
        "retrieved_at",
        "state",
        "response",
        "error",
    }
    if set(value) != required:
        raise ValueError("cache entry has unexpected fields")
    if value["schema_version"] != CACHE_SCHEMA_VERSION:
        raise ValueError("unsupported cache schema version")

    request_key = value["request_key"]
    retrieved_at = value["retrieved_at"]
    error = value["error"]
    kind_value = value["kind"]
    state_value = value["state"]
    if not isinstance(request_key, str) or not request_key:
        raise ValueError("cache request_key must be a non-empty string")
    if retrieved_at is not None and not isinstance(retrieved_at, str):
        raise ValueError("cache retrieved_at must be a string or null")
    if error is not None and not isinstance(error, str):
        raise ValueError("cache error must be a string or null")
    if not isinstance(kind_value, str) or not isinstance(state_value, str):
        raise ValueError("cache kind/state must be strings")

    try:
        kind = CacheKind(kind_value)
        state = CacheState(state_value)
    except ValueError as exc:
        raise ValueError("cache kind/state is invalid") from exc

    if state is CacheState.OK and error is not None:
        raise ValueError("successful cache entries cannot contain an error")
    if state is CacheState.ERROR and not error:
        raise ValueError("error cache entries require an error message")

    return CacheRecord(
        kind=kind,
        request_key=request_key,
        retrieved_at=retrieved_at,
        state=state,
        response=value["response"],
        error=error,
        source=CacheSource.CACHE,
    )
