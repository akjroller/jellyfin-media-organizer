"""Newly supported containers plan exactly like .mkv, end to end."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.inventory import (
    UNSUPPORTED_VIDEO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
)
from jellyfin_show_organizer.models import CompanionStatus, TerminalStatus
from jellyfin_show_organizer.planner import (
    PlanningConfig,
    execute_plan,
)

pytestmark = pytest.mark.local

SERIES = "Example Aired Series"
SEARCH = [{"show": {"id": 4242, "name": SERIES, "premiered": "2024-01-01"}}]
EPISODES = [
    {
        "id": 1000 + number,
        "season": 1,
        "number": number,
        "name": f"Chapter {number}",
        "airdate": f"2024-01-0{number}",
        "type": "regular",
    }
    for number in (1, 2)
]
NEW_FORMATS = (".m2ts", ".m4v", ".mov", ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv")


def _getter(url: str, params: Mapping[str, str] | None = None) -> object:
    del params
    return SEARCH if "search/shows" in url else EPISODES


def _plan(tmp_path: Path, files: dict[str, bytes]):  # type: ignore[no-untyped-def]
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    destination.mkdir(parents=True)
    for relative, payload in files.items():
        path = shows / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit",
            cache_dir=tmp_path / "cache",
        ),
        _getter,
    )


def test_supported_and_unsupported_sets_are_disjoint() -> None:
    assert not VIDEO_EXTENSIONS & UNSUPPORTED_VIDEO_EXTENSIONS
    assert set(NEW_FORMATS) <= VIDEO_EXTENSIONS
    assert {".iso", ".vob"} <= UNSUPPORTED_VIDEO_EXTENSIONS


@pytest.mark.parametrize("extension", NEW_FORMATS)
def test_new_container_is_planned_and_keeps_its_extension(
    tmp_path: Path, extension: str
) -> None:
    outcome = _plan(
        tmp_path, {f"{SERIES}/{SERIES} S01E01{extension.upper()}": b"video"}
    )

    (record,) = outcome.plan.records
    assert record.status is TerminalStatus.MATCHED
    assert record.source.extension == extension
    assert record.destination == (
        f"{SERIES} (2024)/Season 01/{SERIES} (2024) S01E01 - Chapter 1{extension}"
    )
    assert outcome.preflight.ready
    assert outcome.skipped_inventory == ()


def test_subtitle_follows_a_new_container(tmp_path: Path) -> None:
    outcome = _plan(
        tmp_path,
        {
            f"{SERIES}/{SERIES} S01E01.m4v": b"video",
            f"{SERIES}/{SERIES} S01E01.en.srt": b"subtitle",
        },
    )

    (companion,) = outcome.plan.companions
    assert companion.status is CompanionStatus.ASSOCIATED
    assert companion.source_video == f"{SERIES}/{SERIES} S01E01.m4v"
    assert companion.destination == (
        f"{SERIES} (2024)/Season 01/{SERIES} (2024) S01E01 - Chapter 1.en.srt"
    )
    assert outcome.preflight.ready


def test_same_episode_in_two_containers_needs_duplicate_review(
    tmp_path: Path,
) -> None:
    outcome = _plan(
        tmp_path,
        {
            f"{SERIES}/{SERIES} S01E01.mkv": b"matroska-release",
            f"{SERIES}/{SERIES} S01E01.m4v": b"mpeg4-release",
            f"{SERIES}/{SERIES} S01E02.m4v": b"single-release",
        },
    )

    status = {record.source.relative_path: record for record in outcome.plan.records}
    first_mkv = status[f"{SERIES}/{SERIES} S01E01.mkv"]
    first_m4v = status[f"{SERIES}/{SERIES} S01E01.m4v"]
    assert first_mkv.duplicate is not None
    assert first_m4v.duplicate is not None
    assert first_mkv.duplicate.destination_key == first_m4v.duplicate.destination_key
    assert {first_mkv.status, first_m4v.status} & {
        TerminalStatus.DUPLICATE,
        TerminalStatus.SUSPICIOUS,
    }
    assert status[f"{SERIES}/{SERIES} S01E02.m4v"].status is TerminalStatus.MATCHED
    assert not outcome.preflight.ready


def test_disc_structures_stay_skipped(tmp_path: Path) -> None:
    files = {
        f"{SERIES}/{SERIES} S01E01.mkv": b"video",
        f"{SERIES}/Disc 1/BDMV/STREAM/00001.m2ts": b"stream",
        f"{SERIES}/Disc 2/VIDEO_TS/VTS_01_1.VOB": b"dvd",
        f"{SERIES}/Disc 3/{SERIES}.iso": b"image",
    }
    outcome = _plan(tmp_path, files)

    assert [record.source.relative_path for record in outcome.plan.records] == [
        f"{SERIES}/{SERIES} S01E01.mkv"
    ]
    assert outcome.preflight.ready
    reasons = {
        record.relative_path: record.reason for record in outcome.skipped_inventory
    }
    assert reasons == {
        f"{SERIES}/Disc 1/BDMV/STREAM/00001.m2ts": "disc-structure:.m2ts",
        f"{SERIES}/Disc 2/VIDEO_TS/VTS_01_1.VOB": "unsupported-extension:.vob",
        f"{SERIES}/Disc 3/{SERIES}.iso": "unsupported-extension:.iso",
    }


def test_disc_structure_check_uses_folders_below_the_root_only(
    tmp_path: Path,
) -> None:
    shows = tmp_path / "BDMV" / "Shows"
    episode = shows / SERIES / f"{SERIES} S01E01.m2ts"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"video")

    (record,) = scan_videos(authorize_shows_root(shows))

    assert record.status is InventoryStatus.INCLUDED
