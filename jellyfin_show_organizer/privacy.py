"""Shared privacy helpers for user-shareable JMO artifacts."""

from __future__ import annotations

import re

_PRIVATE_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s,;]+")
_PRIVATE_FILE_RE = re.compile(
    r"(?i)\b[^\s,;]+\.(?:mkv|mp4|m4v|avi|mov|wmv|srt|ass|ssa|nfo|jpg|jpeg|png)\b"
)


def path_free_text(value: str) -> str:
    """Redact path-like values before they enter a shareable artifact."""

    redacted = _PRIVATE_PATH_RE.sub("<private-path>", value)
    return _PRIVATE_FILE_RE.sub("<private-file>", redacted)
