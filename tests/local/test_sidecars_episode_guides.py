from __future__ import annotations

from pathlib import Path

import pytest

from jellyfin_show_organizer.inventory import (
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
)
from jellyfin_show_organizer.sidecars import AdjacentDisposition, discover_sidecars

pytestmark = pytest.mark.local


def _touch(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _video_sources(shows: Path):
    records = scan_videos(authorize_shows_root(shows))
    return tuple(
        record.to_source_file()
        for record in records
        if record.status is InventoryStatus.INCLUDED
    )


@pytest.mark.parametrize(
    "filename",
    (
        "Synthetic Series Episode Guide.txt",
        "SYNTHETIC.SERIES.EPISODE_GUIDE.TXT",
        "Synthetic-Series-Season-Guide.txt",
        "Season_Guide.txt",
    ),
)
def test_recognized_episode_guide_documents_are_ignored(
    tmp_path: Path,
    filename: str,
) -> None:
    shows = tmp_path / "Shows"
    _touch(shows / "Synthetic Series" / "Pilot.mkv")
    _touch(shows / "Synthetic Series" / filename, b"documentation")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.unresolved == ()
    assert len(result.ignored) == 1
    ignored = result.ignored[0]
    assert ignored.disposition is AdjacentDisposition.IGNORED
    assert ignored.reason == "known-episode-guide-document"


@pytest.mark.parametrize(
    "filename",
    (
        "release-notes.txt",
        "mystery.txt",
        "episode.txt",
        "guide.txt",
        "episode-guidance.txt",
        "season-overview.txt",
    ),
)
def test_arbitrary_or_near_miss_text_documents_remain_unresolved(
    tmp_path: Path,
    filename: str,
) -> None:
    shows = tmp_path / "Shows"
    _touch(shows / "Synthetic Series" / "Pilot.mkv")
    _touch(shows / "Synthetic Series" / filename, b"unknown")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.ignored == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "unsupported-adjacent-file"


def test_episode_guide_rule_does_not_change_subtitle_association(
    tmp_path: Path,
) -> None:
    shows = tmp_path / "Shows"
    _touch(shows / "Synthetic Series" / "Pilot.mkv")
    _touch(shows / "Synthetic Series" / "Pilot.en.srt", b"subtitle")
    _touch(shows / "Synthetic Series" / "Episode Guide.txt", b"documentation")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.unresolved == ()
    assert len(result.companions) == 1
    assert result.companions[0].source_video == "Synthetic Series/Pilot.mkv"
    assert len(result.ignored) == 1
    assert result.ignored[0].reason == "known-episode-guide-document"
