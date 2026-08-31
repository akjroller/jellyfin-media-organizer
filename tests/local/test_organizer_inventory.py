import os
from pathlib import Path

import pytest

from jellyfin_show_organizer.inventory import (
    InventoryScanError,
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
)

pytestmark = pytest.mark.local


def _touch(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_scan_is_video_only_case_insensitive_and_accounts_for_samples(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Alpha" / "Episode 01.MKV", b"one")
    _touch(shows / "Alpha" / "Episode 02.mp4", b"two")
    _touch(shows / "Alpha" / "Episode 03.avi", b"three")
    _touch(shows / "Alpha" / "Episode 04.srt", b"subtitle")
    _touch(shows / "Alpha" / "poster.jpg", b"art")
    _touch(shows / "Alpha" / "tvshow.nfo", b"metadata")
    _touch(shows / "Alpha" / "Alpha.sample.mkv", b"sample")
    _touch(shows / "Alpha" / "Samples" / "clip.mp4", b"sample-dir")

    records = scan_videos(authorize_shows_root(shows))

    assert [(record.relative_path, record.status) for record in records] == [
        ("Alpha/Alpha.sample.mkv", InventoryStatus.EXCLUDED_SAMPLE),
        ("Alpha/Episode 01.MKV", InventoryStatus.INCLUDED),
        ("Alpha/Episode 02.mp4", InventoryStatus.INCLUDED),
        ("Alpha/Episode 03.avi", InventoryStatus.INCLUDED),
        ("Alpha/Samples/clip.mp4", InventoryStatus.EXCLUDED_SAMPLE),
    ]
    assert all(record.extension in {".avi", ".mkv", ".mp4"} for record in records)
    assert all(record.fingerprint is not None for record in records)


@pytest.mark.skipif(
    os.name == "nt", reason="case-only siblings cannot coexist on Windows"
)
def test_scan_uses_stable_case_insensitive_windows_ordering(tmp_path: Path):
    shows = tmp_path / "Shows"
    for name in ("beta.mkv", "alpha.mkv", "Alpha.mkv", "ALPHA 2.mkv"):
        _touch(shows / "Series" / name)

    records = scan_videos(authorize_shows_root(shows))

    assert [record.relative_path for record in records] == [
        "Series/ALPHA 2.mkv",
        "Series/Alpha.mkv",
        "Series/alpha.mkv",
        "Series/beta.mkv",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem semantics only")
def test_windows_case_only_names_share_one_directory_entry(tmp_path: Path):
    shows = tmp_path / "Shows"
    first = _touch(shows / "Series" / "alpha.mkv", b"first")
    second = _touch(shows / "Series" / "Alpha.mkv", b"second")

    assert first.samefile(second)

    records = scan_videos(authorize_shows_root(shows))

    assert len(records) == 1
    assert records[0].relative_path.casefold() == "series/alpha.mkv"
    assert records[0].fingerprint is not None
    assert records[0].fingerprint.size == len(b"second")


def test_scan_ignores_movies_quarantine_artwork_metadata_and_subtitles_dirs(
    tmp_path: Path,
):
    shows = tmp_path / "Shows"
    _touch(shows / "Series" / "Episode.mkv")
    for directory in ("Movies", "quarantine", "Artwork", "metadata", "Subtitles"):
        _touch(shows / directory / "ignored.mkv")

    records = scan_videos(authorize_shows_root(shows))

    assert [record.relative_path for record in records] == ["Series/Episode.mkv"]


def test_scan_blocks_file_and_directory_symlink_escape(tmp_path: Path):
    shows = tmp_path / "Shows"
    outside = tmp_path / "Outside"
    _touch(shows / "Series" / "Episode.mkv")
    outside_video = _touch(outside / "External.mkv", b"external")

    linked_file = shows / "Series" / "Linked.mkv"
    linked_directory = shows / "Linked Season"
    try:
        linked_file.symlink_to(outside_video)
        linked_directory.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    records = scan_videos(authorize_shows_root(shows))

    assert [(record.relative_path, record.status) for record in records] == [
        ("Series/Episode.mkv", InventoryStatus.INCLUDED),
        ("Series/Linked.mkv", InventoryStatus.BLOCKED_LINK),
    ]
    assert all("External.mkv" not in record.relative_path for record in records)


def test_authorization_rejects_symlink_root(tmp_path: Path):
    shows = tmp_path / "Shows"
    shows.mkdir()
    linked_root = tmp_path / "LinkedShows"
    try:
        linked_root.symlink_to(shows, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(InventoryScanError, match="cannot be a symlink or junction"):
        authorize_shows_root(linked_root)


def test_scanner_requires_explicit_authorization(tmp_path: Path):
    shows = tmp_path / "Shows"
    shows.mkdir()

    with pytest.raises(TypeError, match="explicitly authorized"):
        scan_videos(shows)  # type: ignore[arg-type]


def test_scanner_performs_no_writes(tmp_path: Path):
    shows = tmp_path / "Shows"
    video = _touch(shows / "Series" / "Episode.mkv", b"unchanged")
    before = {
        path.relative_to(shows).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in shows.rglob("*")
        if path.is_file()
    }

    records = scan_videos(authorize_shows_root(shows))

    after = {
        path.relative_to(shows).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in shows.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert video.read_bytes() == b"unchanged"
    assert len(records) == 1
    assert records[0].to_source_file().relative_path == "Series/Episode.mkv"
