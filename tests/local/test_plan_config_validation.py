import tomllib
from pathlib import Path

import pytest

from jellyfin_show_organizer.cli import (
    PLAN_CONFIGURATION_EXIT,
    _planning_config,
    build_parser,
    main,
)
from jellyfin_show_organizer.planner import PlanningConfig

pytestmark = pytest.mark.local


def _write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_plan_config_reports_malformed_toml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    config_path = _write_config(tmp_path / "planning.toml", "schema_version = [\n")
    args = build_parser().parse_args(
        ["plan", str(tmp_path / "Shows"), "--config", str(config_path)]
    )

    with pytest.raises(tomllib.TOMLDecodeError):
        _planning_config(args)

    code = main(
        ["plan", str(tmp_path / "Shows"), "--config", str(config_path), "--verbose"]
    )
    assert code == PLAN_CONFIGURATION_EXIT
    assert "Planning failed safely" in capsys.readouterr().err


def test_plan_config_requires_destination_output_and_cache(tmp_path: Path):
    config_path = _write_config(
        tmp_path / "planning.toml", "schema_version = 1\n\n[plan]\n"
    )
    args = build_parser().parse_args(
        ["plan", str(tmp_path / "Shows"), "--config", str(config_path)]
    )

    with pytest.raises(ValueError, match="destination_root is required"):
        _planning_config(args)


def test_plan_config_rejects_unknown_provider_mode(tmp_path: Path):
    config_path = _write_config(
        tmp_path / "planning.toml",
        """schema_version = 1

[plan]
destination_root = "Organized"
output_dir = "audit"
cache_dir = "cache"
provider_mode = "turbo"
""",
    )
    args = build_parser().parse_args(
        ["plan", str(tmp_path / "Shows"), "--config", str(config_path)]
    )

    with pytest.raises(ValueError, match="config provider_mode is invalid"):
        _planning_config(args)


def test_plan_rejects_output_dir_inside_media_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    shows = tmp_path / "Shows"
    shows.mkdir()
    organized = tmp_path / "Organized"
    organized.mkdir()

    code = main(
        [
            "plan",
            str(shows),
            "--destination-root",
            str(organized),
            "--output-dir",
            str(shows / "audit"),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--verbose",
        ]
    )

    assert code == PLAN_CONFIGURATION_EXIT
    assert "must be outside media roots" in capsys.readouterr().err


def test_plan_rejects_existing_output_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    shows = tmp_path / "Shows"
    shows.mkdir()
    organized = tmp_path / "Organized"
    organized.mkdir()
    output_dir = tmp_path / "audit"
    output_dir.mkdir()

    code = main(
        [
            "plan",
            str(shows),
            "--destination-root",
            str(organized),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--verbose",
        ]
    )

    assert code == PLAN_CONFIGURATION_EXIT
    assert "output directory already exists" in capsys.readouterr().err


def test_planning_config_rejects_offline_and_refresh_together(tmp_path: Path):
    with pytest.raises(
        ValueError, match="offline and refresh modes cannot be enabled together"
    ):
        PlanningConfig(
            shows_root=tmp_path / "Shows",
            destination_root=tmp_path / "Organized",
            output_dir=tmp_path / "audit",
            cache_dir=tmp_path / "cache",
            offline=True,
            refresh=True,
        )
