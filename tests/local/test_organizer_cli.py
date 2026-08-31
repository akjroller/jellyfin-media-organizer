from pathlib import Path
import tomllib

import pytest

from jellyfin_show_organizer.cli import PLAN_NOT_IMPLEMENTED_EXIT, main

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]


def test_organizer_plan_help(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["plan", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "usage: organizer plan" in output
    assert "not implemented" in output


def test_organizer_exposes_no_apply_command(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["apply"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "invalid choice: 'apply'" in error
    assert "plan" in error


def test_organizer_plan_scaffold_fails_without_creating_output(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)

    assert main(["plan"]) == PLAN_NOT_IMPLEMENTED_EXIT

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not implemented yet" in captured.err
    assert (
        "no plan, report, cache, destination directory, or media output was created"
        in captured.err
    )
    assert list(tmp_path.iterdir()) == []


def test_jmo_and_organizer_commands_share_the_same_scaffold_entrypoint():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]

    assert scripts["jmo"] == "jellyfin_show_organizer.cli:main"
    assert scripts["organizer"] == scripts["jmo"]
