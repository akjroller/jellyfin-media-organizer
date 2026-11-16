"""Command adapter for asking a Jellyfin server to rescan its libraries."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, cast

from .jellyfin_server import (
    JELLYFIN_API_KEY_ENV,
    JELLYFIN_URL_ENV,
    JellyfinRefreshError,
    refresh_libraries,
)

JELLYFIN_REFRESH_FAILED_EXIT = 37


def register_jellyfin_commands(subparsers: Any) -> None:
    refresh = subparsers.add_parser(
        "jellyfin-refresh",
        help="Ask your Jellyfin server to rescan its libraries.",
        description=(
            "Send one library-refresh request to your Jellyfin server, for "
            "example after jmo apply. The API key is read only from "
            f"{JELLYFIN_API_KEY_ENV} and is never printed or stored. This "
            "changes nothing on disk."
        ),
    )
    refresh.add_argument(
        "--server",
        help=f"Jellyfin server URL, such as http://localhost:8096 (default: ${JELLYFIN_URL_ENV}).",
    )
    refresh.add_argument("--json", action="store_true", dest="json_output")
    refresh.set_defaults(handler=_run_refresh)


def _run_refresh(args: argparse.Namespace) -> int:
    server = cast(str | None, args.server) or os.environ.get(JELLYFIN_URL_ENV, "")
    try:
        if not server.strip():
            raise JellyfinRefreshError(
                f"pass --server or set {JELLYFIN_URL_ENV} to your Jellyfin URL"
            )
        target = refresh_libraries(server, os.environ.get(JELLYFIN_API_KEY_ENV))
    except JellyfinRefreshError as exc:
        print(f"Jellyfin refresh failed: {exc}", file=sys.stderr)
        return JELLYFIN_REFRESH_FAILED_EXIT
    if bool(args.json_output):
        print(
            json.dumps(
                {"schema_version": 1, "requested": True, "url": target},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        print("Jellyfin library refresh requested; the scan runs on the server.")
    return 0
