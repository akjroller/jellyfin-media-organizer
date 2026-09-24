"""Shared parsing helpers for the line-oriented audit summary artifact."""

from __future__ import annotations

from pathlib import Path


def read_summary(path: Path) -> dict[str, str]:
    """Read ``summary.txt`` key/value pairs without interpreting their values."""

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def summary_int(values: dict[str, str], key: str, *, default: int = 0) -> int:
    """Return one integer summary field with a consistent diagnostic."""

    try:
        return int(values.get(key, str(default)))
    except ValueError as exc:
        raise ValueError(f"audit summary has invalid {key}") from exc
