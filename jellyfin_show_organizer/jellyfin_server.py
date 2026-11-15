"""Ask a Jellyfin server to rescan its libraries after files were organized.

This is the only module that talks to a Jellyfin server.  It sends one
``POST /Library/Refresh`` request authenticated with an API key in Jellyfin's
``Authorization: MediaBrowser Token="..."`` scheme.  The key is read from the
environment by callers, is never placed in a URL, and never appears in any
message or exception this module produces.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

JELLYFIN_URL_ENV = "JMO_JELLYFIN_URL"
JELLYFIN_API_KEY_ENV = "JMO_JELLYFIN_API_KEY"
REFRESH_PATH = "/Library/Refresh"
DEFAULT_TIMEOUT_SECONDS = 15.0

Opener = Callable[..., Any]


class JellyfinRefreshError(RuntimeError):
    """Raised when the library refresh could not be requested."""


def normalize_server_url(value: str) -> str:
    """Return ``scheme://host[:port][/base]`` or refuse anything unusual."""

    candidate = value.strip()
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise JellyfinRefreshError(
            "Jellyfin server URL must look like http://host:8096 or https://host"
        )
    if parts.username or parts.password:
        raise JellyfinRefreshError(
            "Jellyfin server URL must not contain credentials; "
            f"set {JELLYFIN_API_KEY_ENV} instead"
        )
    if parts.query or parts.fragment:
        raise JellyfinRefreshError(
            "Jellyfin server URL must not contain a query or fragment"
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _api_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key:
        raise JellyfinRefreshError(
            f"set {JELLYFIN_API_KEY_ENV} to a Jellyfin API key "
            "(Dashboard > API Keys) to request a library refresh"
        )
    if any(character in key for character in '"\r\n'):
        raise JellyfinRefreshError("Jellyfin API key contains invalid characters")
    return key


def refresh_libraries(
    server_url: str,
    api_key: str | None,
    *,
    opener: Opener | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Ask the server to rescan every library; return the URL that was called."""

    base = normalize_server_url(server_url)
    key = _api_key(api_key)
    target = base + REFRESH_PATH
    request = Request(
        target,
        data=b"",
        method="POST",
        headers={"Authorization": f'MediaBrowser Token="{key}"'},
    )
    try:
        with (opener or urlopen)(request, timeout=timeout) as response:
            status = getattr(response, "status", 204)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise JellyfinRefreshError(
                f"Jellyfin rejected the API key (HTTP {exc.code}); check "
                f"{JELLYFIN_API_KEY_ENV} and that the key belongs to an "
                "administrator"
            ) from None
        raise JellyfinRefreshError(
            f"Jellyfin refused the refresh request (HTTP {exc.code})"
        ) from None
    except URLError as exc:
        raise JellyfinRefreshError(
            f"could not reach the Jellyfin server at {base}: {exc.reason}"
        ) from None
    except TimeoutError:
        raise JellyfinRefreshError(
            f"the Jellyfin server at {base} did not answer in time"
        ) from None
    if not 200 <= int(status) < 300:
        raise JellyfinRefreshError(
            f"Jellyfin refused the refresh request (HTTP {status})"
        )
    return target
