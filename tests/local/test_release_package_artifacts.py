from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.inventory import (
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
)
from jellyfin_show_organizer.models import CompanionStatus
from jellyfin_show_organizer.planner import PlanningConfig, execute_plan
from jellyfin_show_organizer.sidecars import (
    AdjacentDisposition,
    CompanionKind,
    discover_sidecars,
)

pytestmark = pytest.mark.local


RELEASE_ARTIFACT_NAMES = (
    "fabricated-release.rar",
    "FABRICATED-ARCHIVE.RAR",
    "fabricated-part-zero.r00",
    "fabricated-part-one.R01",
    "fabricated-part-last.r99",
    "fabricated-checksum.sfv",
    "FABRICATED-CHECKSUM-UPPER.SFV",
    "fabricated-reconstruction.srr",
    "FABRICATED-RECONSTRUCTION-UPPER.SRR",
)


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


def test_release_package_artifacts_are_ignored_but_unknown_files_fail_closed(
    tmp_path: Path,
) -> None:
    shows = tmp_path / "Shows"
    series = shows / "Fabricated Series"
    _touch(series / "Fabricated Series S01E01.mkv", b"video")
    _touch(series / "Fabricated Series S01E01.en.srt", b"subtitle")
    for name in RELEASE_ARTIFACT_NAMES:
        _touch(series / name, name.encode("ascii"))
    _touch(series / "fabricated-release.par2", b"unknown")

    result = discover_sidecars(
        authorize_shows_root(shows),
        _video_sources(shows),
    )

    assert len(result.companions) == 1
    companion = result.companions[0]
    assert companion.kind is CompanionKind.SUBTITLE
    assert companion.suffix == ".en"

    ignored_paths = {file.relative_path for file in result.ignored}
    expected_paths = {
        f"Fabricated Series/{name}" for name in RELEASE_ARTIFACT_NAMES
    }
    assert ignored_paths == expected_paths
    assert all(
        file.disposition is AdjacentDisposition.IGNORED
        and file.reason == "known-release-package-artifact"
        for file in result.ignored
    )
    assert [(file.relative_path, file.reason) for file in result.unresolved] == [
        (
            "Fabricated Series/fabricated-release.par2",
            "unsupported-adjacent-file",
        )
    ]


SEARCH_RESPONSE = [
    {
        "show": {
            "id": 9090,
            "name": "Fabricated Series",
            "premiered": "2024-01-01",
        }
    }
]
EPISODE_RESPONSE = [
    {
        "id": 909001,
        "season": 1,
        "number": 1,
        "name": "Pilot",
        "airdate": "2024-01-01",
        "type": "regular",
    }
]


class FixtureGetter:
    def __call__(
        self,
        url: str,
        _params: Mapping[str, str] | None = None,
    ) -> object:
        if "search/shows" in url:
            return SEARCH_RESPONSE
        return EPISODE_RESPONSE


def test_release_package_artifacts_are_audited_without_blocking_preflight(
    tmp_path: Path,
) -> None:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    series = shows / "Fabricated Series"
    destination.mkdir()
    _touch(series / "Fabricated Series S01E01.mkv", b"video")
    _touch(series / "Fabricated Series S01E01.en.srt", b"subtitle")
    for name in (
        "fabricated-release.rar",
        "fabricated-release.r00",
        "fabricated-release.r01",
        "fabricated-release.sfv",
        "fabricated-release.srr",
    ):
        _touch(series / name, name.encode("ascii"))

    outcome = execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=destination,
            output_dir=tmp_path / "audit",
            cache_dir=tmp_path / "cache",
        ),
        FixtureGetter(),
    )

    assert outcome.preflight.ready
    ignored = [
        record
        for record in outcome.plan.companions
        if record.status is CompanionStatus.IGNORED
    ]
    assert len(ignored) == 5
    assert all(
        record.reason == "known-release-package-artifact" for record in ignored
    )

    rendered = outcome.bundle.sidecars_csv.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(rendered)))
    ignored_rows = [row for row in rows if row["status"] == "ignored"]
    assert len(ignored_rows) == 5
    assert {row["reason"] for row in ignored_rows} == {
        "known-release-package-artifact"
    }
