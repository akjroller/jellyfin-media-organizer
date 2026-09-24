import json

import pytest

from jellyfin_show_organizer.user_commands import run_inspect, run_report

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


def test_inspect_text_explains_next_step_for_ready_run(tmp_path, capsys):
    run = _bundle(tmp_path)

    assert run_inspect(run, redact_paths=True) == 0
    output = capsys.readouterr().out

    assert "Run: [redacted]" in output
    assert "Next step: run jmo apply" in output


def test_inspect_text_can_show_local_path(tmp_path, capsys):
    run = _bundle(tmp_path)

    assert run_inspect(run) == 0
    assert f"Run: {run.resolve()}" in capsys.readouterr().out


def test_inspect_text_points_to_blocker_artifacts(tmp_path, capsys):
    run = _bundle(tmp_path)
    summary = run / "summary.txt"
    summary.write_text(
        summary.read_text(encoding="utf-8")
        .replace("readiness_state=apply-ready", "readiness_state=blocked")
        .replace("matched=1", "matched=0")
        .replace("extra=1", "extra=0"),
        encoding="utf-8",
    )

    assert run_inspect(run) == 0
    assert "preflight.txt and unresolved.csv" in capsys.readouterr().out


def test_report_bundle_excludes_private_path_and_sensitive_artifacts(tmp_path, capsys):
    run = _bundle(tmp_path)
    output = tmp_path / "shareable-report"

    assert run_report(run, output) == 0
    capsys.readouterr()
    report_json = (output / "report.json").read_text(encoding="utf-8")
    report_text = (output / "report.txt").read_text(encoding="utf-8")

    assert str(run) not in report_json
    assert str(run) not in report_text
    assert "private_paths=excluded" in report_text
    assert "approval_tokens=excluded" in report_text
    assert "provider_cache=excluded" in report_text
    assert "companions=0" in report_text
    assert "associated_companions=0" in report_text
