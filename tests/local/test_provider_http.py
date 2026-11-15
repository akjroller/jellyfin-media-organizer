from __future__ import annotations

import io
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from jellyfin_show_organizer.provider_http import (
    ProviderHttpError,
    ThrottledJsonGetter,
)
from jellyfin_show_organizer.tvmaze_cache import (
    MalformedProviderResponse,
    ProviderFailureKind,
    classify_provider_failure,
)

pytestmark = pytest.mark.local


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _http_error(code: int, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError("https://provider.invalid/x", code, "error", headers, None)


class ScriptedOpener:
    """Return or raise the scripted outcomes in order, recording each request."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> Any:
        del timeout
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome if isinstance(outcome, bytes) else b"[]")


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _getter(opener: ScriptedOpener, clock: FakeClock, **kwargs: Any):
    return ThrottledJsonGetter(
        headers={"User-Agent": "test"},
        min_interval=kwargs.pop("min_interval", 0.0),
        opener=opener,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **kwargs,
    )


def test_success_decodes_json_and_encodes_sorted_query() -> None:
    opener = ScriptedOpener(b'[{"id": 1}]')
    getter = _getter(opener, FakeClock())

    assert getter("https://provider.invalid/search", {"q": "a b", "a": "1"}) == [
        {"id": 1}
    ]
    assert opener.requests[0].full_url == ("https://provider.invalid/search?a=1&q=a+b")


def test_requests_are_spaced_by_the_minimum_interval() -> None:
    clock = FakeClock()
    opener = ScriptedOpener(b"[]", b"[]", b"[]")
    getter = _getter(opener, clock, min_interval=0.5)

    for _ in range(3):
        getter("https://provider.invalid/x")

    assert clock.sleeps == [0.5, 0.5]


def test_rate_limit_honours_retry_after_then_succeeds() -> None:
    clock = FakeClock()
    opener = ScriptedOpener(_http_error(429, retry_after="3"), b'{"ok": true}')
    getter = _getter(opener, clock)

    assert getter("https://provider.invalid/x") == {"ok": True}
    assert clock.sleeps == [3.0]


def test_retry_after_is_capped() -> None:
    clock = FakeClock()
    opener = ScriptedOpener(_http_error(429, retry_after="3600"), b"[]")
    getter = _getter(opener, clock, max_retry_delay=10.0)

    getter("https://provider.invalid/x")

    assert clock.sleeps == [10.0]


def test_server_errors_back_off_exponentially_and_give_up() -> None:
    clock = FakeClock()
    opener = ScriptedOpener(*(_http_error(503) for _ in range(3)))
    getter = _getter(opener, clock, max_attempts=3, backoff=1.0)

    with pytest.raises(ProviderHttpError) as raised:
        getter("https://provider.invalid/x")

    assert raised.value.status_code == 503
    assert clock.sleeps == [1.0, 2.0]
    assert classify_provider_failure(raised.value) is (
        ProviderFailureKind.TRANSIENT_HTTP
    )


def test_not_found_is_not_retried() -> None:
    clock = FakeClock()
    opener = ScriptedOpener(_http_error(404))
    getter = _getter(opener, clock)

    with pytest.raises(ProviderHttpError) as raised:
        getter("https://provider.invalid/x")

    assert len(opener.requests) == 1
    assert clock.sleeps == []
    assert classify_provider_failure(raised.value) is ProviderFailureKind.NOT_FOUND


@pytest.mark.parametrize(
    ("error", "expected_type", "expected_kind"),
    [
        (
            URLError(TimeoutError("timed out")),
            TimeoutError,
            ProviderFailureKind.TIMEOUT,
        ),
        (
            URLError(OSError("name resolution failed")),
            ConnectionError,
            ProviderFailureKind.NETWORK,
        ),
        (TimeoutError("read timed out"), TimeoutError, ProviderFailureKind.TIMEOUT),
    ],
)
def test_transport_errors_are_translated_for_classification(
    error: Exception,
    expected_type: type[Exception],
    expected_kind: ProviderFailureKind,
) -> None:
    opener = ScriptedOpener(error)
    getter = _getter(opener, FakeClock(), max_attempts=1)

    with pytest.raises(expected_type) as raised:
        getter("https://provider.invalid/x")

    assert classify_provider_failure(raised.value) is expected_kind


def test_network_errors_are_retried() -> None:
    clock = FakeClock()
    opener = ScriptedOpener(URLError(OSError("connection reset")), b"[]")
    getter = _getter(opener, clock, backoff=0.25)

    assert getter("https://provider.invalid/x") == []
    assert clock.sleeps == [0.25]


def test_invalid_json_is_a_malformed_response_and_not_retried() -> None:
    opener = ScriptedOpener(b"<html>maintenance</html>")
    getter = _getter(opener, FakeClock())

    with pytest.raises(MalformedProviderResponse) as raised:
        getter("https://provider.invalid/x")

    assert len(opener.requests) == 1
    assert classify_provider_failure(raised.value) is (
        ProviderFailureKind.MALFORMED_RESPONSE
    )


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="min_interval"):
        ThrottledJsonGetter(headers={}, min_interval=-1)
    with pytest.raises(ValueError, match="max_attempts"):
        ThrottledJsonGetter(headers={}, min_interval=0, max_attempts=0)
