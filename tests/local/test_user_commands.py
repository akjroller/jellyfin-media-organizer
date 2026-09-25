from __future__ import annotations

import json
import tomllib
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from jellyfin_show_organizer.user_commands import (
    run_demo,
    run_doctor,
    run_init,
    run_inspect,
    run_report,
    run_review_status,
    run_wizard,
    write_example,
)


def test_doctor_reports_ready_and_json(tmp_path: Path, capsys) -> None:
    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    source.mkdir()
    destination.mkdir()
    (source / "Episode.mkv").write_bytes(b"x")

    assert (
        run_doctor(
            source,
            destination,
            tmp_path / "state",
            tmp_path / "cache",
            json_output=True,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
    assert payload["checks"]


def test_doctor_rejects_state_inside_media(tmp_path: Path, capsys) -> None:
    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    source.mkdir()
    destination.mkdir()
    assert run_doctor(source, destination, source / "state", tmp_path / "cache") == 2
    assert "FIX REQUIRED" in capsys.readouterr().out


def test_doctor_reports_missing_source(tmp_path: Path, capsys) -> None:
    destination = tmp_path / "Organized"
    destination.mkdir()
    assert (
        run_doctor(
            tmp_path / "MissingShows",
            destination,
            tmp_path / "state",
            tmp_path / "cache",
        )
        == 2
    )
    assert "source_exists" in capsys.readouterr().out


def test_init_creates_reusable_state_without_overwriting(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    source.mkdir()
    destination.mkdir()
    state = tmp_path / "state"
    assert run_init(source, destination, state) == 0
    assert (state / "planning.toml").is_file()
    assert (state / "base-overrides.toml").read_text(
        encoding="utf-8"
    ) == "schema_version = 4\n"
    config = tomllib.loads((state / "planning.toml").read_text(encoding="utf-8"))
    assert config["schema_version"] == 1
    assert config["plan"]["overrides"] == "base-overrides.toml"
    assert run_init(source, destination, state) == 2
    assert "refusing" in capsys.readouterr().out.lower()


def test_init_keeps_absolute_paths_when_relative_path_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    state = tmp_path / "state"
    source.mkdir()
    destination.mkdir()

    import jellyfin_show_organizer.user_commands as commands

    original_relpath = commands.os.path.relpath

    def cross_volume_relpath(path, start):
        if Path(path) in {source, destination}:
            raise ValueError("path is on mount D:, start on mount C:")
        return original_relpath(path, start)

    monkeypatch.setattr(commands.os.path, "relpath", cross_volume_relpath)
    assert run_init(source, destination, state) == 0
    config = tomllib.loads((state / "planning.toml").read_text(encoding="utf-8"))
    assert config["plan"]["source_root"] == str(source).replace("\\", "/")
    assert config["plan"]["destination_root"] == str(destination).replace("\\", "/")


def test_init_creates_missing_destination_leaf(tmp_path: Path) -> None:
    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    source.mkdir()
    assert run_init(source, destination, tmp_path / "state") == 0
    assert destination.is_dir()


def test_init_rejects_invalid_roots_and_mode(tmp_path: Path, capsys) -> None:
    source = tmp_path / "Shows"
    source.mkdir()
    missing_destination = tmp_path / "missing"
    assert run_init(source, missing_destination, tmp_path / "state") == 0
    assert missing_destination.is_dir()
    capsys.readouterr()
    destination = tmp_path / "Organized"
    destination.mkdir()
    assert run_init(source, destination, tmp_path / "state", provider_mode="bad") == 2
    assert "provider mode" in capsys.readouterr().out.lower()


def test_demo_creates_only_synthetic_workspace(tmp_path: Path, capsys) -> None:
    output = tmp_path / "demo"
    assert run_demo(output) == 0
    assert (
        output / "Shows" / "Example Show" / "Season 01" / "Example Show - S01E01.mkv"
    ).is_file()
    assert (output / "README.txt").is_file()
    assert (output / "State" / "runs" / "demo-run" / "plan.json").is_file()
    summary = (output / "State" / "runs" / "demo-run" / "summary.txt").read_text(
        encoding="utf-8"
    )
    assert "duplicate_review=2" in summary
    assert "held=1" in summary
    assert "preflight_ready=false" in summary
    assert run_inspect(output / "State" / "runs" / "demo-run") == 0
    assert "blocked" in capsys.readouterr().out
    assert run_demo(output) == 2
    assert "refusing" in capsys.readouterr().out.lower()


def test_inspect_summarizes_run(tmp_path: Path, capsys) -> None:
    (tmp_path / "summary.txt").write_text(
        "records=3\nmatched=2\nextra=1\nduplicate=0\nheld=0\n"
        "suspicious=0\nunresolved=0\nremaining_total=0\n"
        "readiness_state=apply-ready\npreflight_ready=true\n",
        encoding="utf-8",
    )
    assert run_inspect(tmp_path) == 0
    output = capsys.readouterr().out
    assert "apply-ready" in output
    assert "check-only" in output

    assert run_inspect(tmp_path, json_output=True) == 0
    assert json.loads(capsys.readouterr().out)["records"] == 3


def test_inspect_requires_summary(tmp_path: Path, capsys) -> None:
    assert run_inspect(tmp_path) == 2
    assert "summary.txt" in capsys.readouterr().out


def test_inspect_points_blocked_review_runs_to_review(tmp_path: Path, capsys) -> None:
    (tmp_path / "summary.txt").write_text(
        "records=3\nmatched=2\nextra=0\nduplicate=1\nheld=0\n"
        "suspicious=0\nunresolved=0\nremaining_total=1\n"
        "readiness_state=blocked\npreflight_ready=false\n",
        encoding="utf-8",
    )

    assert run_inspect(tmp_path) == 0
    assert "jmo review" in capsys.readouterr().out


def test_review_status_reports_percentages_and_safe_movement_boundary(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_bytes(b"synthetic")
    items = [
        SimpleNamespace(
            state=SimpleNamespace(value="answered"),
            kind=SimpleNamespace(value="duplicate"),
        ),
        SimpleNamespace(
            state=SimpleNamespace(value="pending"), kind=SimpleNamespace(value="held")
        ),
        SimpleNamespace(
            state=SimpleNamespace(value="deferred"), kind=SimpleNamespace(value="held")
        ),
        SimpleNamespace(
            state=SimpleNamespace(value="answered"), kind=SimpleNamespace(value="held")
        ),
    ]
    fake_session = SimpleNamespace(
        items=items,
        sha256="a" * 64,
        plan_sha256="b" * 64,
        approved_scope_refs=(),
        complete=False,
        approved_partial=False,
    )
    monkeypatch.setattr(
        "jellyfin_show_organizer.user_commands.load_review_session",
        lambda _payload: fake_session,
    )

    assert run_review_status(session_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert payload["percentages"] == {
        "answered": 50.0,
        "deferred": 25.0,
        "pending": 25.0,
    }

    assert run_review_status(session_path) == 0
    output = capsys.readouterr().out
    assert "Review categories" in output
    assert "review decisions never move media" in output

    fake_session.complete = True
    assert run_review_status(session_path) == 0
    assert "compile a fresh reviewed plan" in capsys.readouterr().out

    fake_session.complete = False
    fake_session.approved_partial = True
    assert run_review_status(session_path) == 0
    assert "partial review never authorizes apply" in capsys.readouterr().out


def test_review_status_requires_export_inputs(tmp_path: Path, monkeypatch) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_bytes(b"synthetic")
    fake_session = SimpleNamespace(
        items=[],
        sha256="a" * 64,
        plan_sha256="b" * 64,
        approved_scope_refs=(),
        complete=False,
        approved_partial=False,
    )
    monkeypatch.setattr(
        "jellyfin_show_organizer.user_commands.load_review_session",
        lambda _payload: fake_session,
    )

    try:
        run_review_status(session_path, export_path=tmp_path / "export.json")
    except ValueError as exc:
        assert "requires --run-dir" in str(exc)
    else:
        raise AssertionError("review export without a run directory should fail")

    (tmp_path / "run").mkdir()
    try:
        run_review_status(session_path, run_dir=tmp_path / "run")
    except ValueError as exc:
        assert "summary.txt" in str(exc)
    else:
        raise AssertionError("review status should reject an incomplete run")


def test_review_status_can_include_plan_totals_without_private_paths(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_bytes(b"synthetic")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.txt").write_text(
        "records=10\nmatched=5\nextra=1\nduplicate=2\nheld=1\n"
        "suspicious=1\nunresolved=0\ncompanions=4\n"
        "readiness_state=blocked\npreflight_ready=false\n",
        encoding="utf-8",
    )
    fake_session = SimpleNamespace(
        items=[],
        sha256="a" * 64,
        plan_sha256="b" * 64,
        approved_scope_refs=(),
        complete=False,
        approved_partial=False,
    )
    monkeypatch.setattr(
        "jellyfin_show_organizer.user_commands.load_review_session",
        lambda _payload: fake_session,
    )

    assert run_review_status(session_path, run_dir=run_dir, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_summary"]["movable_videos"] == 6
    assert payload["plan_summary"]["untouched_videos"] == 4

    assert run_review_status(session_path, run_dir=run_dir) == 0
    output = capsys.readouterr().out
    assert "Plan totals" in output
    assert "Companions move only" in output


def test_review_status_exports_path_free_evidence_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_bytes(b"synthetic")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.txt").write_text(
        "records=1\nmatched=0\nextra=0\nduplicate=0\nheld=1\n"
        "suspicious=0\nunresolved=0\ncompanions=0\n"
        "readiness_state=apply-ready\npreflight_ready=true\n",
        encoding="utf-8",
    )
    (run_dir / "plan.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source": {"relative_path": "Private/secret.mkv"},
                        "parse": {"season": 1, "episodes": [2]},
                        "show": {"title": "Example", "provider_id": "42"},
                        "evidence": {
                            "method": "synthetic",
                            "confidence": 0.9,
                            "candidates": [{"title": "Example", "score": 0.9}],
                        },
                        "reason": r"C:\\Users\\akjro\\secret.mkv",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    fake_item = SimpleNamespace(
        review_ref="held-1",
        kind=SimpleNamespace(value="held"),
        state=SimpleNamespace(value="pending"),
        show_key="Example",
        source="Private/secret.mkv",
        collision_class=None,
        candidates=(),
    )
    fake_session = SimpleNamespace(
        items=[fake_item],
        sha256="a" * 64,
        plan_sha256="b" * 64,
        approved_scope_refs=(),
        complete=False,
        approved_partial=False,
    )
    monkeypatch.setattr(
        "jellyfin_show_organizer.user_commands.load_review_session",
        lambda _payload: fake_session,
    )

    output = tmp_path / "review-export.json"
    assert run_review_status(session_path, run_dir=run_dir, export_path=output) == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["session_sha256"] == "a" * 64
    assert document["items"][0]["evidence"]["parse"]["season"] == 1
    rendered = output.read_text(encoding="utf-8")
    assert "secret.mkv" not in rendered
    assert "C:\\\\Users" not in rendered


def test_wizard_creates_safe_setup_from_guided_answers(tmp_path: Path) -> None:
    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    state = tmp_path / "JMO-State"
    source.mkdir()
    destination.mkdir()
    answers = iter((str(source), str(destination), str(state), "offline", "Y", "N"))
    output = StringIO()

    assert run_wizard(input_fn=lambda _prompt: next(answers), output=output) == 0
    assert (state / "planning.toml").is_file()
    assert (state / "base-overrides.toml").is_file()
    assert (state / "cache").is_dir()
    assert "never moves, deletes, quarantines" in output.getvalue()


def test_wizard_resumes_existing_setup_without_overwriting_audit_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    state = tmp_path / "JMO-State"
    source.mkdir()
    destination.mkdir()
    assert run_init(source, destination, state, provider_mode="offline") == 0
    sentinel = state / "runs" / "prior-plan.json"
    sentinel.write_text("keep me", encoding="utf-8")

    answers = iter((str(source), str(destination), str(state), "offline", "Y", "Y"))
    output = StringIO()
    assert run_wizard(input_fn=lambda _prompt: next(answers), output=output) == 0

    assert sentinel.read_text(encoding="utf-8") == "keep me"
    text = output.getvalue()
    assert "Existing JMO setup detected" in text
    assert "jmo plan" in text


def test_wizard_ready_path_stops_after_check_only(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    state = tmp_path / "JMO-State"
    source.mkdir()
    destination.mkdir()
    (source / "Episode.mkv").write_bytes(b"synthetic")

    import jellyfin_show_organizer.apply_execution as apply_execution
    import jellyfin_show_organizer.review_contract as review_contract
    import jellyfin_show_organizer.review_system as review_system
    import jellyfin_show_organizer.user_commands as commands

    def fake_outcome():
        return SimpleNamespace(
            plan=SimpleNamespace(records=()),
            preflight=SimpleNamespace(ready=True),
        )

    def fake_execute_plan(config, *args, **kwargs):
        config.output_dir.mkdir(parents=True)
        (config.output_dir / "plan.json").write_text("{}\n", encoding="utf-8")
        (config.output_dir / "plan.sha256").write_text("a" * 64, encoding="ascii")
        (config.output_dir / "preflight.json").write_text("{}\n", encoding="utf-8")
        (config.output_dir / "run-provenance.json").write_text(
            '{"source_revision":{"commit":"' + "b" * 40 + '"}}\n',
            encoding="utf-8",
        )
        return fake_outcome()

    monkeypatch.setattr(commands, "execute_plan", fake_execute_plan)
    monkeypatch.setattr(
        review_contract,
        "load_review_contract",
        lambda _path: SimpleNamespace(snapshot_id="snapshot"),
    )
    monkeypatch.setattr(
        review_system,
        "run_review_system",
        lambda *args, **kwargs: (
            SimpleNamespace(complete=True, sha256="c" * 64),
            b"schema_version = 5\n",
        ),
    )
    monkeypatch.setattr(
        review_contract,
        "load_review_contract",
        lambda _path: SimpleNamespace(snapshot_id="snapshot"),
    )

    check_only_calls: list[bool] = []
    monkeypatch.setattr(
        apply_execution,
        "prepare_apply",
        lambda *args, **kwargs: SimpleNamespace(),
    )

    def fake_apply(*args, **kwargs):
        check_only_calls.append(bool(kwargs["check_only"]))
        return SimpleNamespace(groups_total=1)

    monkeypatch.setattr(
        apply_execution,
        "execute_apply",
        fake_apply,
    )
    monkeypatch.setattr(apply_execution, "total_moving_members", lambda _prepared: 1)
    monkeypatch.setattr(
        apply_execution, "approval_token", lambda *args, **kwargs: "token"
    )
    import jellyfin_show_organizer.apply_validation as apply_validation
    import jellyfin_show_organizer.run_provenance as run_provenance

    monkeypatch.setattr(
        apply_validation,
        "validate_apply_roots",
        lambda source_root, destination_root: (source_root, destination_root),
    )
    monkeypatch.setattr(
        run_provenance,
        "detect_source_revision",
        lambda: SimpleNamespace(state="git", dirty=False, commit="b" * 40),
    )

    output = StringIO()
    answers = iter((str(source), str(destination), str(state), "offline", "Y", "Y"))
    assert run_wizard(input_fn=lambda _prompt: next(answers), output=output) == 0
    assert check_only_calls == [True]
    assert "read-only boundary" in output.getvalue()
    assert not list(state.rglob("apply-journal.jsonl"))


def test_write_example_refuses_overwrite(tmp_path: Path, capsys) -> None:
    target = tmp_path / "example.toml"
    assert write_example(target, "x\n") == 0
    assert write_example(target, "y\n") == 2
    assert target.read_text(encoding="utf-8") == "x\n"
    assert "Refusing" in capsys.readouterr().out


def test_report_rejects_invalid_or_existing_output(tmp_path: Path) -> None:
    missing_summary = tmp_path / "missing"
    missing_summary.mkdir()
    try:
        run_report(missing_summary, tmp_path / "out")
    except ValueError as exc:
        assert "summary.txt" in str(exc)
    else:
        raise AssertionError("missing summary should be rejected")

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "summary.txt").write_text("records=not-a-number\n", encoding="utf-8")
    try:
        run_report(invalid, tmp_path / "out")
    except ValueError as exc:
        assert "records" in str(exc)
    else:
        raise AssertionError("invalid count should be rejected")

    valid = tmp_path / "valid"
    valid.mkdir()
    (valid / "summary.txt").write_text("records=0\n", encoding="utf-8")
    existing = tmp_path / "existing"
    existing.mkdir()
    try:
        run_report(valid, existing)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("existing output should be rejected")


def test_write_example_can_print_and_reject_missing_parent(
    capsys, tmp_path: Path
) -> None:
    assert write_example(None, "schema_version = 1\n") == 0
    assert "schema_version" in capsys.readouterr().out
    assert write_example(tmp_path / "missing" / "example.toml", "x\n") == 2
    assert "does not exist" in capsys.readouterr().out
