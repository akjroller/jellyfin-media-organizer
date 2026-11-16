"""Series-level artwork and tvshow.nfo move with their show, or stay put safely."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import (
    CanonicalShow,
    CompanionStatus,
    MatchEvidence,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.planner import (
    PlanningConfig,
    PlanningOutcome,
    _plan_show_files,
    execute_plan,
)
from jellyfin_show_organizer.sidecars import AdjacentDisposition, AdjacentFile

pytestmark = pytest.mark.local

SHOWS = {
    "alpha series": {"id": 11, "name": "Alpha Series", "premiered": "2020-01-01"},
    "beta series": {"id": 22, "name": "Beta Series", "premiered": "2021-01-01"},
}


def _episodes(show_id: int) -> list[dict[str, object]]:
    return [
        {
            "id": show_id * 100 + season * 10 + number,
            "season": season,
            "number": number,
            "name": f"Chapter {season}-{number}",
            "airdate": f"20{show_id}-0{season}-0{number}",
            "type": "regular",
        }
        for season in (1, 2)
        for number in (1, 2)
    ]


def _getter(url: str, params: Mapping[str, str] | None = None) -> object:
    if "search/shows" in url:
        query = dict(params or {}).get("q", "")
        return [{"show": show} for key, show in SHOWS.items() if key == query]
    for show in SHOWS.values():
        if f"/shows/{show['id']}/episodes" in url:
            return _episodes(int(str(show["id"])))
    return []


def _plan(
    tmp_path: Path, files: dict[str, bytes], existing: tuple[str, ...] = ()
) -> PlanningOutcome:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    destination.mkdir(parents=True)
    for relative, payload in files.items():
        path = shows / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    for relative in existing:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"already organized")
    return execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit",
            cache_dir=tmp_path / "cache",
        ),
        _getter,
    )


def _companions(outcome: PlanningOutcome) -> dict[str, tuple[str, str | None, str]]:
    return {
        companion.relative_path: (
            companion.status.value,
            companion.destination,
            companion.reason,
        )
        for companion in outcome.plan.companions
    }


def test_show_files_in_a_season_tree_move_to_the_show_folder(tmp_path: Path) -> None:
    outcome = _plan(
        tmp_path,
        {
            "Alpha Series/Season 1/Alpha Series S01E01.mkv": b"v1",
            "Alpha Series/Season 2/Alpha Series S02E01.mkv": b"v2",
            "Alpha Series/Poster.JPG": b"poster",
            "Alpha Series/tvshow.nfo": b"<tvshow/>",
            "Alpha Series/Season 1/poster.jpg": b"season poster",
        },
    )

    assert outcome.preflight.ready
    companions = _companions(outcome)
    assert companions["Alpha Series/Poster.JPG"][:2] == (
        "associated",
        "Alpha Series (2020)/poster.jpg",
    )
    assert companions["Alpha Series/tvshow.nfo"][:2] == (
        "associated",
        "Alpha Series (2020)/tvshow.nfo",
    )
    # Season-level art is not series-level: it stays, reported as before.
    assert companions["Alpha Series/Season 1/poster.jpg"] == (
        "ignored",
        None,
        "explicitly-ignored-adjacent-file",
    )
    anchors = {
        companion.source_video
        for companion in outcome.plan.companions
        if companion.status is CompanionStatus.ASSOCIATED
    }
    assert anchors == {"Alpha Series/Season 1/Alpha Series S01E01.mkv"}


def test_two_source_folders_bringing_the_same_file_both_stay(tmp_path: Path) -> None:
    outcome = _plan(
        tmp_path,
        {
            "Alpha Series S1/Alpha Series S01E01.mkv": b"v1",
            "Alpha Series S1/poster.jpg": b"poster one",
            "Alpha Series S2/Alpha Series S02E01.mkv": b"v2",
            "Alpha Series S2/poster.jpg": b"poster two",
            "Alpha Series S2/fanart.jpg": b"fanart",
        },
    )

    assert outcome.preflight.ready
    companions = _companions(outcome)
    assert companions["Alpha Series S1/poster.jpg"] == (
        "ignored",
        None,
        "show-file-collision",
    )
    assert companions["Alpha Series S2/poster.jpg"][2] == "show-file-collision"
    assert companions["Alpha Series S2/fanart.jpg"][:2] == (
        "associated",
        "Alpha Series (2020)/fanart.jpg",
    )


def test_existing_organized_artwork_is_never_replaced(tmp_path: Path) -> None:
    outcome = _plan(
        tmp_path,
        {
            "Alpha Series/Alpha Series S01E01.mkv": b"v1",
            "Alpha Series/poster.jpg": b"new poster",
        },
        existing=("Alpha Series (2020)/poster.jpg",),
    )

    assert outcome.preflight.ready
    assert _companions(outcome)["Alpha Series/poster.jpg"] == (
        "ignored",
        None,
        "destination-show-file-exists",
    )


def test_a_mixed_folder_keeps_its_show_files(tmp_path: Path) -> None:
    outcome = _plan(
        tmp_path,
        {
            "Mixed/Alpha Series S01E01.mkv": b"a",
            "Mixed/Beta Series S01E01.mkv": b"b",
            "Mixed/poster.jpg": b"which show?",
        },
    )

    # The folder is resolved as one show and fails, so nothing in it moves.
    assert _companions(outcome)["Mixed/poster.jpg"][0] == "ignored"


def test_a_folder_whose_episodes_resolve_to_two_shows_keeps_its_files() -> None:
    def matched(path: str, show_id: int, folder: str) -> PlanRecord:
        return PlanRecord(
            source=SourceFile(path, ".mkv", SourceFingerprint(1, 1)),
            status=TerminalStatus.MATCHED,
            parse=ParseResult(),
            show=CanonicalShow(folder, tvmaze_id=show_id, title=folder),
            evidence=MatchEvidence(method="test", confidence=1.0),
            destination=f"{folder}/Season 01/{folder} S01E01.mkv",
            operation_group_id=f"op-{show_id}",
        )

    poster = AdjacentFile(
        "Mixed/poster.jpg",
        ".jpg",
        SourceFingerprint(1, 1),
        AdjacentDisposition.IGNORED,
        "show-level-file",
    )

    (planned,) = _plan_show_files(
        (poster,),
        (matched("Mixed/a.mkv", 11, "Alpha"), matched("Mixed/b.mkv", 22, "Beta")),
        None,
    )

    assert planned.status is CompanionStatus.IGNORED
    assert planned.reason == "show-folder-has-several-shows"


def test_show_files_never_block_when_no_episode_moves(tmp_path: Path) -> None:
    outcome = _plan(
        tmp_path,
        {
            "Unknown Program/Unknown Program S01E01.mkv": b"x",
            "Unknown Program/poster.jpg": b"poster",
        },
    )

    assert _companions(outcome)["Unknown Program/poster.jpg"] == (
        "ignored",
        None,
        "show-folder-has-no-moving-episode",
    )
    assert not any(
        "Unknown Program/poster.jpg" in finding.record_ids
        for finding in outcome.preflight.findings
    )
