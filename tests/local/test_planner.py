from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from jellyfin_show_organizer.cli import PLAN_SUCCESS_EXIT, main
from jellyfin_show_organizer.models import CompanionStatus, TerminalStatus
from jellyfin_show_organizer.planner import (
    PlanningConfig,
    PlanningConfigurationError,
    execute_plan,
)
from jellyfin_show_organizer.tvmaze_cache import JsonGetter

pytestmark = pytest.mark.local


SEARCH_RESPONSE = [
    {
        "show": {
            "id": 4242,
            "name": "Example Aired Series",
            "premiered": "2024-01-01",
        }
    }
]
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


class CountingGetter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        return SEARCH_RESPONSE if "search/shows" in url else EPISODE_RESPONSE


def _library(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    shows = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    series = shows / "Example Aired Series"
    series.mkdir(parents=True)
    destination.mkdir()
    video_payload = b"fabricated-video"
    subtitle_payload = b"fabricated-subtitle"
    (series / "Example Aired Series S01E01.mkv").write_bytes(video_payload)
    (series / "Example Aired Series S01E01.en.srt").write_bytes(subtitle_payload)
    return shows, destination, video_payload, subtitle_payload


def _config(
    tmp_path: Path,
    shows: Path,
    destination: Path,
    *,
    output_name: str,
    offline: bool = False,
) -> PlanningConfig:
    return PlanningConfig(
        shows_root=shows,
        destination_root=destination,
        output_dir=tmp_path / output_name,
        cache_dir=tmp_path / "cache",
        offline=offline,
    )


def test_end_to_end_plan_is_cached_deterministic_and_non_mutating(
    tmp_path: Path,
) -> None:
    shows, destination, video_payload, subtitle_payload = _library(tmp_path)
    getter = CountingGetter()
    first = execute_plan(
        _config(tmp_path, shows, destination, output_name="audit-first"),
        getter,
    )

    assert first.preflight.ready
    assert not first.provider_failure
    assert len(getter.calls) == 2
    assert [record.status for record in first.plan.records] == [TerminalStatus.MATCHED]
    assert len(first.plan.records[0].provider_episodes) == 1
    assert first.plan.records[0].provider_episodes[0].tvmaze_episode_id == 1001
    assert [record.status for record in first.plan.companions] == [
        CompanionStatus.ASSOCIATED
    ]
    assert len(first.plan.provenance.cache_snapshots) == 2  # type: ignore[union-attr]
    assert list(destination.iterdir()) == []
    assert (
        shows / "Example Aired Series" / "Example Aired Series S01E01.mkv"
    ).read_bytes() == video_payload
    assert (
        shows / "Example Aired Series" / "Example Aired Series S01E01.en.srt"
    ).read_bytes() == subtitle_payload

    output_files = {path.name for path in (tmp_path / "audit-first").iterdir()}
    assert output_files == {
        "duplicates.csv",
        "extras.csv",
        "mapping.csv",
        "plan.json",
        "plan.sha256",
        "preflight.json",
        "preflight.txt",
        "sidecars.csv",
        "summary.txt",
        "unresolved.csv",
    }

    def reject_network(
        _url: str,
        _params: Mapping[str, str] | None = None,
    ) -> object:
        raise AssertionError("offline replay attempted provider access")

    second = execute_plan(
        _config(
            tmp_path,
            shows,
            destination,
            output_name="audit-offline",
            offline=True,
        ),
        cast(JsonGetter, reject_network),
    )

    assert second.preflight.ready
    assert first.preflight.plan_hash == second.preflight.plan_hash
    assert first.bundle.plan_json == second.bundle.plan_json


def test_planner_rejects_generated_state_inside_media_root(tmp_path: Path) -> None:
    shows, destination, _, _ = _library(tmp_path)
    config = PlanningConfig(
        shows_root=shows,
        destination_root=destination,
        output_dir=shows / "audit",
        cache_dir=tmp_path / "cache",
    )

    with pytest.raises(PlanningConfigurationError, match="outside media roots"):
        execute_plan(config, CountingGetter())

    assert not (shows / "audit").exists()


def test_cli_emits_stable_json_summary_without_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shows, destination, _, _ = _library(tmp_path)
    getter = CountingGetter()

    def run_with_fixture(config: PlanningConfig):
        return execute_plan(config, getter)

    monkeypatch.setattr("jellyfin_show_organizer.cli.execute_plan", run_with_fixture)
    output_dir = tmp_path / "audit-cli"
    exit_code = main(
        [
            "plan",
            str(shows),
            "--destination-root",
            str(destination),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--json",
        ]
    )

    assert exit_code == PLAN_SUCCESS_EXIT
    payload = json.loads(capsys.readouterr().out)
    assert payload["preflight_ready"] is True
    assert payload["exit_code"] == PLAN_SUCCESS_EXIT
    rendered = json.dumps(payload)
    assert str(tmp_path) not in rendered
    assert output_dir.is_dir()
