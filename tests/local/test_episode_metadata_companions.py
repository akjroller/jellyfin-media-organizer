"""Episode .nfo files and -thumb images travel with their video."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.apply_contract import (
    ApplyContract,
    _derive_operation_groups,
)
from jellyfin_show_organizer.apply_execution import PreparedApply, execute_apply
from jellyfin_show_organizer.inventory import authorize_shows_root
from jellyfin_show_organizer.models import (
    CompanionStatus,
    SourceFile,
    SourceFingerprint,
)
from jellyfin_show_organizer.planner import (
    PlanningConfig,
    execute_plan,
)
from jellyfin_show_organizer.rollback_execution import (
    execute_rollback,
    prepare_rollback,
)
from jellyfin_show_organizer.sidecars import (
    AdjacentDisposition,
    CompanionKind,
    discover_sidecars,
)

pytestmark = pytest.mark.local

SERIES = "Example Aired Series"
SEARCH = [{"show": {"id": 4242, "name": SERIES, "premiered": "2024-01-01"}}]
EPISODES = [
    {
        "id": 1001,
        "season": 1,
        "number": 1,
        "name": "Pilot",
        "airdate": "2024-01-01",
        "type": "regular",
    }
]
STEM = f"{SERIES} S01E01"
NEW_STEM = f"{SERIES} (2024)/Season 01/{SERIES} (2024) S01E01 - Pilot"


def _getter(url: str, params: Mapping[str, str] | None = None) -> object:
    del params
    return SEARCH if "search/shows" in url else EPISODES


def _write(root: Path, relative: str, payload: bytes = b"x") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _video(relative: str) -> SourceFile:
    return SourceFile(
        relative_path=relative,
        extension=Path(relative).suffix.casefold(),
        fingerprint=SourceFingerprint(size=1, mtime_ns=1),
    )


def test_episode_files_are_matched_exactly_and_show_files_are_separate(
    tmp_path: Path,
) -> None:
    shows = tmp_path / "Shows"
    for name in (
        f"{STEM}.mkv",
        f"{STEM}.nfo",
        f"{STEM.upper()}-THUMB.JPG",
        f"{STEM}-thumb.png",
        f"{STEM}.jpg",
        f"{STEM}-thumb.txt",
        "tvshow.nfo",
        "season.nfo",
        "poster.jpg",
        "Unrelated Episode.nfo",
    ):
        _write(shows, f"{SERIES}/{name}")

    discovery = discover_sidecars(
        authorize_shows_root(shows), (_video(f"{SERIES}/{STEM}.mkv"),)
    )

    associated = {
        group.files[0].relative_path.rsplit("/", 1)[1]: group.kind
        for group in discovery.companions
    }
    assert associated == {
        f"{STEM}.nfo": CompanionKind.METADATA,
        f"{STEM.upper()}-THUMB.JPG": CompanionKind.THUMBNAIL,
        f"{STEM}-thumb.png": CompanionKind.THUMBNAIL,
    }
    ignored = {file.relative_path.rsplit("/", 1)[1] for file in discovery.ignored}
    assert {f"{STEM}.jpg", "season.nfo", "Unrelated Episode.nfo"} <= ignored
    assert {file.relative_path.rsplit("/", 1)[1] for file in discovery.show_files} == {
        "tvshow.nfo",
        "poster.jpg",
    }
    unresolved = {
        file.relative_path.rsplit("/", 1)[1]: file.reason
        for file in discovery.unresolved
    }
    assert unresolved == {f"{STEM}-thumb.txt": "unsupported-adjacent-file"}


def test_metadata_shared_by_two_containers_stays_in_place(tmp_path: Path) -> None:
    shows = tmp_path / "Shows"
    for name in (f"{STEM}.mkv", f"{STEM}.m4v", f"{STEM}.nfo", f"{STEM}-thumb.jpg"):
        _write(shows, f"{SERIES}/{name}")

    discovery = discover_sidecars(
        authorize_shows_root(shows),
        (_video(f"{SERIES}/{STEM}.mkv"), _video(f"{SERIES}/{STEM}.m4v")),
    )

    assert discovery.companions == ()
    assert {file.reason for file in discovery.ignored} == {"ambiguous-episode-metadata"}
    assert all(
        file.disposition is AdjacentDisposition.IGNORED for file in discovery.ignored
    )


def _plan(tmp_path: Path):  # type: ignore[no-untyped-def]
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    destination.mkdir(parents=True)
    _write(shows, f"{SERIES}/{STEM}.mkv", b"video")
    _write(shows, f"{SERIES}/{STEM}.nfo", b"<episodedetails/>")
    _write(shows, f"{SERIES}/{STEM}-thumb.jpg", b"jpeg")
    _write(shows, f"{SERIES}/tvshow.nfo", b"<tvshow/>")
    outcome = execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit",
            cache_dir=tmp_path / "cache",
        ),
        _getter,
    )
    return shows, destination, outcome


def test_plan_renames_episode_files_with_their_video(tmp_path: Path) -> None:
    _shows, _destination, outcome = _plan(tmp_path)

    assert outcome.preflight.ready
    by_kind = {
        companion.kind: companion
        for companion in outcome.plan.companions
        if companion.status is CompanionStatus.ASSOCIATED
    }
    assert by_kind["metadata"].destination == f"{NEW_STEM}.nfo"
    assert by_kind["thumbnail"].destination == f"{NEW_STEM}-thumb.jpg"
    (video,) = outcome.plan.records
    assert {companion.operation_group_id for companion in by_kind.values()} == {
        video.operation_group_id
    }
    show_level = [
        companion
        for companion in outcome.plan.companions
        if companion.relative_path.endswith("tvshow.nfo")
    ]
    assert [
        (companion.status, companion.kind, companion.destination)
        for companion in show_level
    ] == [
        (
            CompanionStatus.ASSOCIATED,
            "show-metadata",
            f"{SERIES} (2024)/tvshow.nfo",
        )
    ]


def test_apply_moves_and_rollback_restores_episode_files(tmp_path: Path) -> None:
    shows, destination, outcome = _plan(tmp_path)
    manifest = json.loads(outcome.bundle.plan_json)
    groups = _derive_operation_groups(manifest, separate_roots=True)
    prepared = PreparedApply(
        contract=ApplyContract(
            plan_sha256=outcome.preflight.plan_hash,
            groups=groups,
            separate_roots=True,
        ),
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
    )
    journal = tmp_path / "apply.jsonl"

    execute_apply(prepared, shows, destination, journal_path=journal)

    assert (destination / f"{NEW_STEM}.mkv").read_bytes() == b"video"
    assert (destination / f"{NEW_STEM}.nfo").read_bytes() == b"<episodedetails/>"
    assert (destination / f"{NEW_STEM}-thumb.jpg").read_bytes() == b"jpeg"
    assert not (shows / SERIES / f"{STEM}.nfo").exists()
    assert (destination / f"{SERIES} (2024)" / "tvshow.nfo").read_bytes() == (
        b"<tvshow/>"
    )
    assert not (shows / SERIES / "tvshow.nfo").exists()

    rollback = prepare_rollback(prepared, journal)
    execute_rollback(
        rollback,
        shows,
        destination,
        rollback_journal_path=tmp_path / "rollback.jsonl",
    )

    assert (shows / SERIES / f"{STEM}.mkv").read_bytes() == b"video"
    assert (shows / SERIES / f"{STEM}.nfo").read_bytes() == b"<episodedetails/>"
    assert (shows / SERIES / f"{STEM}-thumb.jpg").read_bytes() == b"jpeg"
    assert not (destination / f"{NEW_STEM}.nfo").exists()
    assert (shows / SERIES / "tvshow.nfo").read_bytes() == b"<tvshow/>"
