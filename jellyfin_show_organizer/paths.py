"""Shared filesystem path checks used by every stage that touches media roots.

Inventory, sidecar discovery, preflight, apply, rollback, and quarantine must
agree on what counts as a link, so they share one fail-closed definition.
"""

from __future__ import annotations

from pathlib import Path


def is_junction(path: Path) -> bool:
    """Whether ``path`` is a Windows junction; uncertain answers count as yes."""

    checker = getattr(path, "is_junction", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return True


def is_link(path: Path, *, entry_is_symlink: bool = False) -> bool:
    """Whether ``path`` is a symlink or junction that must not be followed.

    ``entry_is_symlink`` lets directory walkers pass the answer they already
    have from ``os.DirEntry``. Any filesystem error while checking counts as a
    link, so callers refuse the path instead of following it.
    """

    if entry_is_symlink:
        return True
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    return is_junction(path)
