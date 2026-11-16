"""Video-like files JMO does not plan must be reported, never silently dropped."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer import review_execution
from jellyfin_show_organizer.api import inspect_audit
from jellyfin_show_organizer.audit_commands import (
    run_inspect,
    run_report,
)
from jellyfin_show_organizer.cli import PLAN_SUCCESS_EXIT, main
from jellyfin_show_organizer.inventory import (
    UNSUPPORTED_VIDEO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
    skipped_inventory_records,
)
from jellyfin_show_organizer.models import CompanionStatus
from jellyfin_show_organizer.planner import (
    PlanningConfig,
    PlanningOutcome,
    execute_plan,
)

pytestmark = pytest.mark.local

SERIES = "Example Aired Series"
SEARCH_RESPONSE = [{"show": {"id": 4242, "name": SERIES, "premiered": "2024-01-01"}}]
EPISODE_RESPONSE = [
    {
        "id": 1001,
        "season": 1,
        "number": 1,
        "name": "Pilot",
        "airdate": "2024-01-01",
        "type": "regular",
    }
]


def _getter(url: str, params: Mapping[str, str] | None = None) -> object:
    del params
    return SEARCH_RESPONSE if "search/shows" in url else EPISODE_RESPONSE


def _touch(path: Path, content: bytes = b"fabricated") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _library(tmp_path: Path, *, with_skipped: bool) -> tuple[Path, Path]:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    destination.mkdir(parents=True)
    _touch(shows / SERIES / f"{SERIES} S01E01.mkv")
    if with_skipped:
        _touch(shows / SERIES / f"{SERIES} S01E02.flv")
        _touch(shows / SERIES / f"{SERIES} S01E03.VOB")
        _touch(shows / SERIES / "Sample" / f"{SERIES} S01E01.mkv")
    return shows, destination


def _config(tmp_path: Path, shows: Path, destination: Path, name: str):
    return PlanningConfig(
        shows_root=shows,
        destination_root=destination,
        output_dir=tmp_path / name,
        cache_dir=tmp_path / "cache",
    )


def test_supported_and_unsupported_extension_sets_do_not_overlap() -> None:
    assert not VIDEO_EXTENSIONS & UNSUPPORTED_VIDEO_EXTENSIONS
    assert all(
        ext.startswith(".") and ext == ext.casefold()
        for ext in UNSUPPORTED_VIDEO_EXTENSIONS
    )


def test_scan_reports_unsupported_video_containers(tmp_path: Path) -> None:
    shows, _ = _library(tmp_path, with_skipped=True)
    _touch(shows / SERIES / "poster.jpg")
    _touch(shows / SERIES / "notes.txt")

    records = scan_videos(authorize_shows_root(shows))
    by_path = {record.relative_path: record for record in records}

    flv = by_path[f"{SERIES}/{SERIES} S01E02.flv"]
    assert flv.status is InventoryStatus.UNSUPPORTED_VIDEO
    assert flv.reason == "unsupported-extension:.flv"
    assert flv.fingerprint is None
    vob = by_path[f"{SERIES}/{SERIES} S01E03.VOB"]
    assert vob.status is InventoryStatus.UNSUPPORTED_VIDEO
    assert vob.extension == ".vob"
    assert f"{SERIES}/poster.jpg" not in by_path
    assert f"{SERIES}/notes.txt" not in by_path
    with pytest.raises(ValueError):
        flv.to_source_file()

    skipped = skipped_inventory_records(records)
    assert {record.status for record in skipped} == {
        InventoryStatus.UNSUPPORTED_VIDEO,
        InventoryStatus.EXCLUDED_SAMPLE,
    }
    assert len(skipped) == 3


def test_skipped_files_are_reported_and_do_not_block_the_plan(tmp_path: Path) -> None:
    clean_shows, clean_destination = _library(tmp_path / "clean", with_skipped=False)
    clean = execute_plan(
        _config(tmp_path / "clean", clean_shows, clean_destination, "audit"),
        _getter,
    )
    shows, destination = _library(tmp_path / "mixed", with_skipped=True)
    mixed = execute_plan(
        _config(tmp_path / "mixed", shows, destination, "audit"), _getter
    )

    # Unsupported containers next to a planned video stay put as ignored
    # companions; they neither join nor block the planned video's move.
    assert mixed.preflight.ready

    def planned(outcome: PlanningOutcome) -> list[tuple[str, str, str | None]]:
        return [
            (record.source.relative_path, record.status.value, record.destination)
            for record in outcome.plan.records
        ]

    assert planned(mixed) == planned(clean)
    assert len(mixed.skipped_inventory) == 3
    ignored = {
        companion.relative_path: companion
        for companion in mixed.plan.companions
        if companion.status is CompanionStatus.IGNORED
    }
    assert set(ignored) == {
        f"{SERIES}/{SERIES} S01E02.flv",
        f"{SERIES}/{SERIES} S01E03.VOB",
    }
    assert {companion.reason for companion in ignored.values()} == {
        "unsupported-video-container"
    }
    assert all(companion.destination is None for companion in ignored.values())

    rows = list(
        csv.DictReader(
            io.StringIO(
                (tmp_path / "mixed" / "audit" / "skipped.csv").read_text(
                    encoding="utf-8-sig"
                )
            )
        )
    )
    assert [(row["source"], row["status"]) for row in rows] == [
        (f"{SERIES}/{SERIES} S01E02.flv", "unsupported-video"),
        (f"{SERIES}/{SERIES} S01E03.VOB", "unsupported-video"),
        (f"{SERIES}/Sample/{SERIES} S01E01.mkv", "excluded-sample"),
    ]
    clean_rows = (tmp_path / "clean" / "audit" / "skipped.csv").read_text(
        encoding="utf-8-sig"
    )
    assert clean_rows == "source,extension,status,reason\n"

    summary = inspect_audit(tmp_path / "mixed" / "audit")
    assert summary.skipped_unsupported_videos == 2
    assert summary.skipped_samples == 1
    assert inspect_audit(tmp_path / "clean" / "audit").skipped_unsupported_videos == 0


def test_live_cli_planning_path_reports_skipped_files(tmp_path: Path) -> None:
    shows, destination = _library(tmp_path, with_skipped=True)

    outcome = review_execution.execute_plan(
        _config(tmp_path, shows, destination, "audit"), _getter
    )

    assert len(outcome.skipped_inventory) == 3
    assert (tmp_path / "audit" / "skipped.csv").is_file()
    assert "skipped_unsupported_videos=2" in outcome.bundle.summary_txt.decode()


def _run_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
) -> int:
    shows, destination = _library(tmp_path, with_skipped=True)

    def run_with_fixture(config: PlanningConfig, **_kwargs: object):
        return review_execution.execute_plan(config, _getter)

    monkeypatch.setattr("jellyfin_show_organizer.cli.execute_plan", run_with_fixture)
    return main(
        [
            "plan",
            str(shows),
            "--destination-root",
            str(destination),
            "--output-dir",
            str(tmp_path / "audit"),
            "--cache-dir",
            str(tmp_path / "cache"),
            *extra,
        ]
    )


def test_cli_warns_about_unsupported_videos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_cli(tmp_path, monkeypatch) == PLAN_SUCCESS_EXIT

    out = capsys.readouterr().out
    assert "Skipped 2 video file(s)" in out
    assert "(.flv, .vob)" in out
    assert "skipped.csv" in out


def test_cli_json_summary_counts_skipped_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_cli(tmp_path, monkeypatch, "--json") == PLAN_SUCCESS_EXIT

    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped"] == {"samples": 1, "unsupported_videos": 2}


def test_inspect_and_report_surface_skipped_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_cli(tmp_path, monkeypatch) == PLAN_SUCCESS_EXIT
    capsys.readouterr()

    assert run_inspect(tmp_path / "audit") == 0
    assert "Unsupported videos 2 (not organized; see skipped.csv)" in (
        capsys.readouterr().out
    )
    assert run_inspect(tmp_path / "audit", json_output=True) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["skipped_unsupported_videos"] == 2
    assert inspected["skipped_samples"] == 1

    assert run_report(tmp_path / "audit", tmp_path / "report") == 0
    report_text = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "report").iterdir()
        if path.suffix == ".json"
    )
    assert '"skipped_unsupported_videos": 2' in report_text or (
        '"skipped_unsupported_videos":2' in report_text
    )
    assert str(tmp_path) not in report_text
