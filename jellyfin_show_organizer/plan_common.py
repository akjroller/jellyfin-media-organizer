"""Errors and path keys shared by every planning module."""

from __future__ import annotations

import hashlib
import unicodedata


class PlanningConfigurationError(ValueError):
    """Raised when a requested plan cannot be run within the safety contract."""


def path_key(value: str) -> tuple[str, str]:
    return unicodedata.normalize("NFKC", value).casefold(), value


def operation_group_id(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFKC", relative_path).casefold()
    return "op-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
