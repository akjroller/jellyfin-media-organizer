from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from .tvmaze_cache import JsonGetter, TvmazeCatalogCache

TVMAZE_AKAS_URL = "https://api.tvmaze.com/shows/{tvmaze_id}/akas"
_ALIAS_CACHE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class AliasCacheRecord:
    request_key: str
    response: object | None
    error: str | None
    source: str

    @property
    def resolved(self) -> bool:
        return self.error is None

    @property
    def unresolved_reason(self) -> str | None:
        return self.error

    @property
    def snapshot_id(self) -> str:
        payload = json.dumps(
            {
                "schema_version": _ALIAS_CACHE_SCHEMA_VERSION,
                "request_key": self.request_key,
                "response": self.response,
                "error": self.error,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TvmazeAliasCache:
    """Small deterministic cache for TVMaze AKA responses.

    The main TVMaze catalog cache owns the cache root and policy switches. Alias
    data uses a separate schema so older search/episode cache records remain fully
    compatible. A warm or offline lookup never calls the provider.
    """

    def __init__(self, catalog_cache: TvmazeCatalogCache) -> None:
        self._root = catalog_cache.root / "akas"
        self._offline = catalog_cache.offline
        self._refresh = catalog_cache.refresh

    def show_aliases(self, tvmaze_id: int, getter: JsonGetter) -> AliasCacheRecord:
        if tvmaze_id <= 0:
            raise ValueError("tvmaze_id must be positive")
        request_key = f"akas:{tvmaze_id}"
        path = self._root / f"{tvmaze_id}.json"
        cached = self._read(path, request_key)
        if cached is not None and (self._offline or not self._refresh):
            return cached
        if self._offline:
            return AliasCacheRecord(
                request_key=request_key,
                response=None,
                error="offline alias cache miss",
                source="policy",
            )

        url = TVMAZE_AKAS_URL.format(tvmaze_id=tvmaze_id)
        try:
            response = getter(url, None)
        except Exception as exc:
            record = AliasCacheRecord(
                request_key=request_key,
                response=None,
                error=f"provider alias request failed: {type(exc).__name__}: {exc}",
                source="network",
            )
        else:
            if not isinstance(response, list) or any(
                not isinstance(item, dict) for item in response
            ):
                record = AliasCacheRecord(
                    request_key=request_key,
                    response=None,
                    error="malformed provider alias response",
                    source="network",
                )
            else:
                record = AliasCacheRecord(
                    request_key=request_key,
                    response=response,
                    error=None,
                    source="network",
                )
        self._write(path, record)
        return record

    def _read(self, path: Path, request_key: str) -> AliasCacheRecord | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("alias cache entry must be an object")
            value = cast(dict[str, Any], raw)
            if set(value) != {
                "schema_version",
                "request_key",
                "response",
                "error",
            }:
                raise ValueError("alias cache entry has unexpected fields")
            if value["schema_version"] != _ALIAS_CACHE_SCHEMA_VERSION:
                raise ValueError("unsupported alias cache schema version")
            if value["request_key"] != request_key:
                raise ValueError("alias cache request identity mismatch")
            error = value["error"]
            if error is not None and not isinstance(error, str):
                raise ValueError("alias cache error must be a string or null")
            response = value["response"]
            if error is None and (
                not isinstance(response, list)
                or any(not isinstance(item, dict) for item in response)
            ):
                raise ValueError("alias cache response must be a JSON object array")
            if error is not None and response is not None:
                raise ValueError("failed alias cache entries cannot carry responses")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return AliasCacheRecord(
                request_key=request_key,
                response=None,
                error=f"corrupt alias cache entry: {type(exc).__name__}: {exc}",
                source="cache",
            )
        return AliasCacheRecord(
            request_key=request_key,
            response=response,
            error=error,
            source="cache",
        )

    def _write(self, path: Path, record: AliasCacheRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "schema_version": _ALIAS_CACHE_SCHEMA_VERSION,
                "request_key": record.request_key,
                "response": record.response,
                "error": record.error,
            },
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
