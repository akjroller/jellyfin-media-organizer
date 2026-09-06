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


def test_exact_episode_proper_marker_is_ignored_as_release_artifact(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Example.Series.S07E07.mkv")
    _touch(shows / "Example Series" / "Example.Series.S07E07=proper")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.unresolved == ()
    assert len(result.ignored) == 1
    adjacent = result.ignored[0]
    assert adjacent.relative_path == "Example Series/Example.Series.S07E07=proper"
    assert adjacent.disposition is AdjacentDisposition.IGNORED
    assert adjacent.reason == "known-release-marker-artifact"


def test_proper_word_without_episode_coordinate_remains_unresolved(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "release=proper")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.ignored == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "unsupported-adjacent-file"


def test_proper_marker_with_real_extension_is_not_blanket_ignored(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "Example.Series.S07E07=proper.txt")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.ignored == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "unsupported-adjacent-file"
