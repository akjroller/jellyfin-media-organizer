"""Library refresh sends exactly one authenticated request and never leaks the key."""

from __future__ import annotations

import io
import json
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from jellyfin_show_organizer import jellyfin_server
from jellyfin_show_organizer.entrypoint import main
from jellyfin_show_organizer.jellyfin_cli import JELLYFIN_REFRESH_FAILED_EXIT
from jellyfin_show_organizer.jellyfin_server import (
    JellyfinRefreshError,
    normalize_server_url,
    refresh_libraries,
)

pytestmark = pytest.mark.local

KEY = "0123456789abcdef0123456789abcdef"


class _Response(io.BytesIO):
    status = 204

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class Recorder:
    def __init__(self, outcome: object = None) -> None:
        self.outcome = outcome
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> Any:
        del timeout
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        response = _Response()
        if isinstance(self.outcome, int):
            response.status = self.outcome
        return response


def _http_error(code: int) -> HTTPError:
    return HTTPError("http://jellyfin.invalid", code, "error", Message(), None)


def test_refresh_posts_to_library_refresh_with_mediabrowser_token() -> None:
    recorder = Recorder()

    target = refresh_libraries(" http://media.invalid:8096/jf/ ", KEY, opener=recorder)

    (request,) = recorder.requests
    assert target == "http://media.invalid:8096/jf/Library/Refresh"
    assert request.full_url == target
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f'MediaBrowser Token="{KEY}"'
    assert KEY not in request.full_url


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://media.invalid", "must look like"),
        ("media.invalid:8096", "must look like"),
        ("http://user:secret@media.invalid", "must not contain credentials"),
        ("http://media.invalid/?api_key=x", "query or fragment"),
        ("http://media.invalid/#x", "query or fragment"),
    ],
)
def test_unusual_server_urls_are_refused(url: str, message: str) -> None:
    with pytest.raises(JellyfinRefreshError, match=message):
        normalize_server_url(url)


@pytest.mark.parametrize("key", [None, "", "  ", 'a"b', "a\nb"])
def test_missing_or_malformed_keys_are_refused_before_any_request(
    key: str | None,
) -> None:
    recorder = Recorder()

    with pytest.raises(JellyfinRefreshError):
        refresh_libraries("http://media.invalid", key, opener=recorder)
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (_http_error(401), "rejected the API key"),
        (_http_error(403), "rejected the API key"),
        (_http_error(500), "HTTP 500"),
        (URLError(ConnectionRefusedError("refused")), "could not reach"),
        (TimeoutError("timed out"), "did not answer in time"),
        (302, "HTTP 302"),
    ],
)
def test_failures_are_explained_without_the_key(outcome: object, message: str) -> None:
    with pytest.raises(JellyfinRefreshError, match=message) as raised:
        refresh_libraries("http://media.invalid", KEY, opener=Recorder(outcome))
    assert KEY not in str(raised.value)
    assert raised.value.__cause__ is None


def test_cli_reads_server_and_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(jellyfin_server, "urlopen", recorder)
    monkeypatch.setenv("JMO_JELLYFIN_URL", "http://media.invalid:8096")
    monkeypatch.setenv("JMO_JELLYFIN_API_KEY", KEY)

    assert main(["jellyfin-refresh"]) == 0
    assert "library refresh requested" in capsys.readouterr().out

    assert (
        main(["jellyfin-refresh", "--server", "https://other.invalid", "--json"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "requested": True,
        "schema_version": 1,
        "url": "https://other.invalid/Library/Refresh",
    }
    assert len(recorder.requests) == 2


def test_cli_failures_never_print_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("JMO_JELLYFIN_URL", raising=False)
    monkeypatch.setenv("JMO_JELLYFIN_API_KEY", KEY)

    assert main(["jellyfin-refresh"]) == JELLYFIN_REFRESH_FAILED_EXIT
    assert "pass --server" in capsys.readouterr().err

    monkeypatch.setattr(jellyfin_server, "urlopen", Recorder(_http_error(401)))
    assert (
        main(["jellyfin-refresh", "--server", "http://media.invalid"])
        == JELLYFIN_REFRESH_FAILED_EXIT
    )
    captured = capsys.readouterr()
    assert "rejected the API key" in captured.err
    assert KEY not in captured.err + captured.out
