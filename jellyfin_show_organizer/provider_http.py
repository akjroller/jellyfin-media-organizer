"""Polite, classified JSON HTTP access for metadata providers.

Provider caches classify failures by exception type (``TimeoutError``,
``ConnectionError``, :class:`ProviderHttpError` with ``status_code``, and
:class:`MalformedProviderResponse`).  ``urllib`` raises none of those
directly, so this module translates its errors, spaces requests to respect
provider rate limits, and retries only transient failures with bounded delays.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .tvmaze_cache import MalformedProviderResponse

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_RETRY_DELAY_SECONDS = 30.0
# TVMaze allows roughly 20 calls per 10 seconds per client.
TVMAZE_MIN_INTERVAL_SECONDS = 0.5
TMDB_MIN_INTERVAL_SECONDS = 0.1

Opener = Callable[..., Any]


class ProviderHttpError(RuntimeError):
    """A provider answered with a non-success HTTP status."""

    def __init__(
        self, status_code: int, message: str, *, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _parse_retry_after(value: str | None) -> float | None:
    """Return a Retry-After delay in seconds; HTTP-date forms are ignored."""

    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _is_timeout(reason: object) -> bool:
    return isinstance(reason, TimeoutError) or "timed out" in str(reason).casefold()


class ThrottledJsonGetter:
    """A :class:`~.tvmaze_cache.JsonGetter` with spacing, retries, and typed errors."""

    def __init__(
        self,
        *,
        headers: Mapping[str, str],
        min_interval: float,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        backoff: float = DEFAULT_BACKOFF_SECONDS,
        max_retry_delay: float = DEFAULT_MAX_RETRY_DELAY_SECONDS,
        opener: Opener = urlopen,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval cannot be negative")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._headers = dict(headers)
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._timeout = timeout
        self._backoff = backoff
        self._max_retry_delay = max_retry_delay
        self._opener = opener
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None

    def __call__(self, url: str, params: Mapping[str, str] | None = None) -> object:
        query = urlencode(sorted((params or {}).items()))
        target = f"{url}?{query}" if query else url
        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            try:
                return self._request_once(target)
            except (ProviderHttpError, TimeoutError, ConnectionError) as exc:
                delay = self._retry_delay(exc, attempt)
                if delay is None or attempt == self._max_attempts:
                    raise
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _throttle(self) -> None:
        now = self._monotonic()
        if self._last_request is not None:
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                self._sleep(wait)
                now = self._monotonic()
        self._last_request = now

    def _request_once(self, target: str) -> object:
        request = Request(target, headers=self._headers)
        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            headers = exc.headers
            retry_after = _parse_retry_after(
                headers.get("Retry-After") if headers is not None else None
            )
            raise ProviderHttpError(
                exc.code, f"provider HTTP {exc.code}", retry_after=retry_after
            ) from exc
        except URLError as exc:
            if _is_timeout(exc.reason):
                raise TimeoutError(f"provider request timed out: {exc.reason}") from exc
            raise ConnectionError(f"provider connection failed: {exc.reason}") from exc
        except OSError as exc:
            if isinstance(exc, TimeoutError | ConnectionError):
                raise
            raise ConnectionError(f"provider connection failed: {exc}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise MalformedProviderResponse(
                f"provider response is not valid JSON: {type(exc).__name__}"
            ) from exc

    def _retry_delay(self, error: Exception, attempt: int) -> float | None:
        backoff = min(self._max_retry_delay, self._backoff * 2 ** (attempt - 1))
        if isinstance(error, ProviderHttpError):
            if error.status_code == 429:
                if error.retry_after is not None:
                    return min(self._max_retry_delay, error.retry_after)
                return backoff
            if 500 <= error.status_code <= 599:
                return backoff
            return None
        if isinstance(error, TimeoutError | ConnectionError):
            return backoff
        return None
