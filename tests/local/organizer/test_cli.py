import pytest

from mnamer.organizer.cli import build_parser, main

pytestmark = pytest.mark.local


def test_plan_help_is_available_without_media_access(
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["plan", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "read-only organization plan" in output


def test_plan_fails_closed_until_implemented(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["plan"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "no files were read or changed" in error
