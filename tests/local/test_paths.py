from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import cast

import pytest

from jellyfin_show_organizer import paths
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.planner import path_key

pytestmark = pytest.mark.local

_NO_JUNCTION_SUPPORT = object()


class _Entry:
    """Stands in for a Path so each link check can be scripted."""

    def __init__(
        self,
        *,
        symlink: bool | Exception = False,
        junction: bool | Exception | object = False,
    ) -> None:
        self._symlink = symlink
        if junction is not _NO_JUNCTION_SUPPORT:
            self.is_junction = lambda: self._answer(junction)

    @staticmethod
    def _answer(value: object) -> bool:
        if isinstance(value, Exception):
            raise value
        return bool(value)

    def is_symlink(self) -> bool:
        return self._answer(self._symlink)


def _entry(**scripted: object) -> Path:
    return cast(Path, _Entry(**scripted))  # type: ignore[arg-type]


def test_regular_file_and_directory_are_not_links(tmp_path: Path) -> None:
    file = tmp_path / "file.mkv"
    file.write_bytes(b"x")

    assert not paths.is_link(file)
    assert not paths.is_link(tmp_path)


def test_directory_walker_answer_short_circuits() -> None:
    assert paths.is_link(_entry(), entry_is_symlink=True)


@pytest.mark.parametrize(
    ("symlink", "junction", "expected"),
    [
        (True, False, True),
        (False, True, True),
        (False, False, False),
        (False, _NO_JUNCTION_SUPPORT, False),
        (PermissionError("denied"), False, True),
        (False, OSError("unreadable reparse point"), True),
    ],
)
def test_link_checks_fail_closed(
    symlink: bool | Exception, junction: object, expected: bool
) -> None:
    assert paths.is_link(_entry(symlink=symlink, junction=junction)) is expected


def test_junction_check_without_platform_support_is_false() -> None:
    assert paths.is_junction(_entry(junction=_NO_JUNCTION_SUPPORT)) is False
    assert paths.is_junction(_entry(junction=OSError("x"))) is True


def test_real_symlink_is_a_link(tmp_path: Path) -> None:
    target = tmp_path / "target.mkv"
    target.write_bytes(b"x")
    link = tmp_path / "link.mkv"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    assert paths.is_link(link)


def test_override_sources_match_filenames_in_either_unicode_form(
    tmp_path: Path,
) -> None:
    """macOS filesystems may report decomposed (NFD) names; overrides are typed
    composed (NFC). Both sides are folded the same way, so they still match."""

    composed = unicodedata.normalize("NFC", "Café Stories/Café S01E01.mkv")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed

    overrides = tmp_path / "overrides.toml"
    overrides.write_text(
        "schema_version = 4\n\n[[source_holds]]\n"
        f'source = "{composed}"\n'
        'reasons = ["synthetic unicode form check"]\n',
        encoding="utf-8",
    )
    catalog = load_overrides(overrides)

    assert catalog.source_hold_for(decomposed) is not None
    assert catalog.source_hold_for(composed.upper()) is not None
    assert path_key(decomposed)[0] == path_key(composed)[0]
