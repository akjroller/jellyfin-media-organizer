import tomllib
from pathlib import Path

import pytest

from jellyfin_show_organizer import __version__
from jellyfin_show_organizer.cli import _planning_config, build_parser, main

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]


def test_organizer_version(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"Jellyfin Media Organizer {__version__}"


def test_organizer_plan_help(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["plan", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    normalized_output = " ".join(output.split())
    assert "usage: organizer plan" in output
    assert "non-mutating audit bundle" in normalized_output
    assert "--destination-root" in output
    assert "--output-dir" in output
    assert "--cache-dir" in output
    assert "--offline" in output


def test_organizer_exposes_no_apply_command(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["apply"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "invalid choice: 'apply'" in error
    assert "plan" in error


def test_organizer_plan_requires_explicit_paths_without_creating_output(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["plan"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert exc_info.value.code == 2
    assert "required" in captured.err
    assert list(tmp_path.iterdir()) == []


def test_jmo_and_organizer_commands_share_the_same_entrypoint():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]

    assert scripts["jmo"] == "jellyfin_show_organizer.cli:main"
    assert scripts["organizer"] == scripts["jmo"]


def test_plan_config_paths_are_relative_to_config_and_cli_wins(tmp_path: Path):
    config_dir = tmp_path / "configuration"
    config_dir.mkdir()
    config_path = config_dir / "planning.toml"
    config_path.write_text(
        """schema_version = 1

[plan]
destination_root = "../Organized"
output_dir = "../audit-from-config"
cache_dir = "../cache"
provider_mode = "offline"
max_path_length = 220
max_component_length = 170
""",
        encoding="utf-8",
    )
    cli_output = tmp_path / "audit-from-cli"
    args = build_parser().parse_args(
        [
            "plan",
            str(tmp_path / "Shows"),
            "--config",
            str(config_path),
            "--output-dir",
            str(cli_output),
            "--online",
        ]
    )

    config = _planning_config(args)

    assert config.destination_root == config_dir / "../Organized"
    assert config.output_dir == cli_output
    assert config.cache_dir == config_dir / "../cache"
    assert not config.offline
    assert not config.refresh
    assert config.max_path_length == 220
    assert config.max_component_length == 170


def test_plan_config_rejects_unknown_fields(tmp_path: Path):
    config_path = tmp_path / "planning.toml"
    config_path.write_text(
        """schema_version = 1

[plan]
destination_root = "Organized"
output_dir = "audit"
cache_dir = "cache"
unexpected = true
""",
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        ["plan", str(tmp_path / "Shows"), "--config", str(config_path)]
    )

    with pytest.raises(ValueError, match="unknown fields"):
        _planning_config(args)
