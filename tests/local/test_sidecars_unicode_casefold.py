from pathlib import Path

import pytest

from jellyfin_show_organizer.inventory import (
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
)
from jellyfin_show_organizer.sidecars import CompanionKind, discover_sidecars

pytestmark = pytest.mark.local


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _video_sources(shows: Path):
    records = scan_videos(authorize_shows_root(shows))
    return tuple(
        record.to_source_file()
        for record in records
        if record.status is InventoryStatus.INCLUDED
    )


@pytest.mark.parametrize(
    ("video_name", "subtitle_name", "expected_suffix"),
    [
        (
            "Straße.S01E01.mkv",
            "STRASSE.S01E01.en.forced.srt",
            ".en.forced",
        ),
        (
            "STRASSE.S01E01.mkv",
            "Straße.S01E01.de.sdh.srt",
            ".de.sdh",
        ),
    ],
)
def test_subtitle_suffix_boundary_survives_casefold_length_change(
    tmp_path: Path,
    video_name: str,
    subtitle_name: str,
    expected_suffix: str,
) -> None:
    shows = tmp_path / "Shows"
    series = shows / "Fabricated Series"
    _touch(series / video_name)
    _touch(series / subtitle_name)

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.unresolved == ()
    assert result.ignored == ()
    assert len(result.companions) == 1
    companion = result.companions[0]
    assert companion.kind is CompanionKind.SUBTITLE
    assert companion.source_video == f"Fabricated Series/{video_name}"
    assert companion.suffix == expected_suffix
    assert companion.files[0].relative_path == f"Fabricated Series/{subtitle_name}"


def test_casefold_boundary_fix_does_not_enable_fuzzy_prefix_matching(
    tmp_path: Path,
) -> None:
    shows = tmp_path / "Shows"
    series = shows / "Fabricated Series"
    _touch(series / "Straße.S01E01.mkv")
    _touch(series / "STRASSEE.S01E01.en.srt")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.companions == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "no-deterministic-video-association"
