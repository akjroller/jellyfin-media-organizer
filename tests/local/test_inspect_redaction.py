import json

import pytest

from jellyfin_show_organizer.user_commands import run_inspect

pytestmark = pytest.mark.local


def _bundle(tmp_path):
    run = tmp_path / "private" / "audit"
    run.mkdir(parents=True)
    (run / "summary.txt").write_text(
        "\n".join(
            (
                "readiness_state=apply-ready",
                "preflight_ready=true",
                "records=2",
                "matched=1",
                "extra=1",
                "duplicate=0",
                "held=0",
                "suspicious=0",
                "unresolved=0",
                "remaining_total=0",
                "plan_sha256=" + "a" * 64,
            )
        ),
        encoding="utf-8",
    )
    return run


def test_inspect_json_redacts_local_path(tmp_path, capsys):
    run = _bundle(tmp_path)

    assert run_inspect(run, json_output=True, redact_paths=True) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "run_dir" not in payload
    assert payload["readiness_state"] == "apply-ready"


def test_inspect_json_keeps_path_for_local_use(tmp_path, capsys):
    run = _bundle(tmp_path)

    assert run_inspect(run, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["run_dir"] == str(run.resolve())
