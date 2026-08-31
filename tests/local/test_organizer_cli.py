import pytest

from jellyfin_show_organizer.cli import main

pytestmark = pytest.mark.local


def test_organizer_plan_help(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["plan", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "usage: organizer plan" in output
    assert "planning workflow" in output


def test_organizer_exposes_no_apply_command(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["apply"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "invalid choice: 'apply'" in error
    assert "plan" in error


def test_organizer_plan_scaffold_is_non_mutating(
    capsys: pytest.CaptureFixture[str],
):
    assert main(["plan"]) == 0
    output = capsys.readouterr().out
    assert "does not read, move, rename, or delete media files" in output
