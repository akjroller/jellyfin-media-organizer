from pathlib import Path

import pytest

from jellyfin_show_organizer.inventory import (
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
)
from jellyfin_show_organizer.sidecars import (
    AdjacentDisposition,
    CompanionKind,
    companion_destinations,
    discover_sidecars,
)

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


def test_discovers_supported_subtitles_and_preserves_suffixes(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "Pilot.srt")
    _touch(shows / "Example Series" / "Pilot.en.srt")
    _touch(shows / "Example Series" / "Pilot.eng.default.ass")
    _touch(shows / "Example Series" / "Pilot.ja.forced.ssa")
    _touch(shows / "Example Series" / "Pilot.en.sdh.vtt")
    _touch(shows / "Example Series" / "Pilot.cc.srt")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.unresolved == ()
    assert result.ignored == ()
    assert [
        (group.kind, group.suffix, tuple(file.extension for file in group.files))
        for group in result.companions
    ] == [
        (CompanionKind.SUBTITLE, "", (".srt",)),
        (CompanionKind.SUBTITLE, ".cc", (".srt",)),
        (CompanionKind.SUBTITLE, ".en", (".srt",)),
        (CompanionKind.SUBTITLE, ".en.sdh", (".vtt",)),
        (CompanionKind.SUBTITLE, ".eng.default", (".ass",)),
        (CompanionKind.SUBTITLE, ".ja.forced", (".ssa",)),
    ]


def test_idx_and_sub_are_one_logical_companion_group(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "Pilot.en.idx")
    _touch(shows / "Example Series" / "Pilot.en.sub")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert len(result.companions) == 1
    group = result.companions[0]
    assert group.kind is CompanionKind.SUBTITLE_PAIR
    assert group.source_video == "Example Series/Pilot.mkv"
    assert group.suffix == ".en"
    assert tuple(file.extension for file in group.files) == (".idx", ".sub")


def test_idx_without_sub_pair_remains_unresolved(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "Pilot.idx")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.companions == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "idx-subtitle-missing-sub-pair"


def test_ambiguous_subtitle_relationship_is_never_guessed(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "Pilot.en.mkv")
    _touch(shows / "Example Series" / "Pilot.en.srt")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.companions == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "ambiguous-subtitle-association"


def test_unrecognized_subtitle_suffix_remains_unresolved(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "Pilot.commentary.srt")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert result.companions == ()
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "no-deterministic-video-association"


def test_adjacent_metadata_artwork_and_unknown_files_have_explicit_policy(
    tmp_path: Path,
):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "tvshow.nfo")
    _touch(shows / "Example Series" / "poster.jpg")
    _touch(shows / "Example Series" / "fanart.PNG")
    _touch(shows / "Example Series" / "release-notes.txt")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert {
        (file.relative_path, file.disposition, file.reason) for file in result.ignored
    } == {
        (
            "Example Series/fanart.PNG",
            AdjacentDisposition.IGNORED,
            "explicitly-ignored-adjacent-file",
        ),
        (
            "Example Series/poster.jpg",
            AdjacentDisposition.IGNORED,
            "explicitly-ignored-adjacent-file",
        ),
        (
            "Example Series/tvshow.nfo",
            AdjacentDisposition.IGNORED,
            "explicitly-ignored-adjacent-file",
        ),
    }
    assert [(file.relative_path, file.reason) for file in result.unresolved] == [
        ("Example Series/release-notes.txt", "unsupported-adjacent-file")
    ]


def test_companion_destination_follows_chosen_video_destination(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")
    _touch(shows / "Example Series" / "Pilot.en.forced.srt")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )
    group = result.companions[0]

    assert companion_destinations(
        "Example Series (2024)/Season 01/Example Series - S01E01 - Pilot.mkv",
        group,
    ) == (
        "Example Series (2024)/Season 01/"
        "Example Series - S01E01 - Pilot.en.forced.srt",
    )


def test_sidecar_discovery_performs_no_media_writes(tmp_path: Path):
    shows = tmp_path / "Shows"
    video = _touch(shows / "Example Series" / "Pilot.mkv", b"video")
    subtitle = _touch(shows / "Example Series" / "Pilot.en.srt", b"subtitle")
    before = {
        path.relative_to(shows).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in shows.rglob("*")
        if path.is_file()
    }

    discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    after = {
        path.relative_to(shows).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in shows.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert video.read_bytes() == b"video"
    assert subtitle.read_bytes() == b"subtitle"


def test_sidecar_discovery_requires_explicit_root_authorization(tmp_path: Path):
    shows = tmp_path / "Shows"
    _touch(shows / "Example Series" / "Pilot.mkv")

    with pytest.raises(TypeError, match="explicitly authorized"):
        discover_sidecars(shows, _video_sources(shows))  # type: ignore[arg-type]
