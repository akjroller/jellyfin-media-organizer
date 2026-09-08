from __future__ import annotations

import json
from pathlib import Path

import pytest

from jellyfin_show_organizer import entrypoint
from jellyfin_show_organizer.apply_contract import ApplyContract
from jellyfin_show_organizer.apply_execution import PreparedApply
from jellyfin_show_organizer.rollback_execution import (
    PreparedRollback,
    RollbackExecutionResult,
)
from jellyfin_show_organizer.run_provenance import SourceRevision

pytestmark = pytest.mark.local


def _rollback_paths(tmp_path: Path) -> tuple[list[str], PreparedApply, PreparedRollback]:
    plan = tmp_path / "plan.json"
    preflight = tmp_path / "preflight.json"
    provenance = tmp_path / "run-provenance.json"
    apply_journal = tmp_path / "apply.jsonl"
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    for path in (plan, preflight, provenance, apply_journal):
        path.write_text("{}\n", encoding="utf-8")
    source.mkdir()
    destination.mkdir()
    prepared_apply = PreparedApply(
        contract=ApplyContract(plan_sha256="a" * 64, groups=()),
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
    )
    prepared_rollback = PreparedRollback(
        prepared_apply=prepared_apply,
        apply_journal_path=apply_journal,
        apply_journal_sha256="d" * 64,
    )
    args = [
        "rollback",
        str(plan),
        "--preflight",
        str(preflight),
        "--run-provenance",
        str(provenance),
        "--apply-journal",
        str(apply_journal),
        "--source-root",
        str(source),
        "--destination-root",
        str(destination),
        "--approve-plan-sha256",
        "a" * 64,
        "--approve-review-session-sha256",
        "b" * 64,
        "--approve-source-revision",
        "c" * 40,
    ]
    return args, prepared_apply, prepared_rollback


def _result(*, check_only: bool, journal: Path | None = None) -> RollbackExecutionResult:
    return RollbackExecutionResult(
        plan_sha256="a" * 64,
        review_session_sha256="b" * 64,
        apply_journal_sha256="d" * 64,
        groups_total=0,
        groups_completed=0,
        members_restored=0,
        members_recovered=0,
        rollback_journal_path=journal,
        check_only=check_only,
    )


def _patch_valid_boundary(
    monkeypatch: pytest.MonkeyPatch,
    prepared_apply: PreparedApply,
    prepared_rollback: PreparedRollback,
) -> None:
    monkeypatch.setattr(
        entrypoint,
        "prepare_apply",
        lambda *_args, **_kwargs: prepared_apply,
    )
    monkeypatch.setattr(
        entrypoint,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, False),
    )
    monkeypatch.setattr(
        entrypoint,
        "prepare_rollback",
        lambda *_args, **_kwargs: prepared_rollback,
    )
    monkeypatch.setattr(entrypoint, "rollback_token", lambda *_args: "ROLLBACK-TOKEN")


def test_rollback_check_only_cli_returns_exact_token_as_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args, prepared_apply, prepared_rollback = _rollback_paths(tmp_path)
    _patch_valid_boundary(monkeypatch, prepared_apply, prepared_rollback)
    monkeypatch.setattr(
        entrypoint,
        "execute_rollback",
        lambda *_args, **_kwargs: _result(check_only=True),
    )

    assert entrypoint.main([*args, "--check-only", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["check_only"] is True
    assert payload["confirmation_token"] == "ROLLBACK-TOKEN"
    assert payload["apply_journal_sha256"] == "d" * 64


def test_rollback_cli_requires_exact_confirmation_and_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args, prepared_apply, prepared_rollback = _rollback_paths(tmp_path)
    _patch_valid_boundary(monkeypatch, prepared_apply, prepared_rollback)
    rollback_journal = tmp_path / "rollback.jsonl"
    monkeypatch.setattr(
        entrypoint,
        "execute_rollback",
        lambda *_args, **_kwargs: _result(
            check_only=False,
            journal=rollback_journal,
        ),
    )

    assert (
        entrypoint.main(
            [
                *args,
                "--rollback-journal",
                str(rollback_journal),
                "--confirm-rollback",
                "WRONG",
            ]
        )
        == entrypoint.ROLLBACK_FAILED_EXIT
    )
    assert "confirmation does not match" in capsys.readouterr().err

    assert (
        entrypoint.main(
            [
                *args,
                "--rollback-journal",
                str(rollback_journal),
                "--confirm-rollback",
                "ROLLBACK-TOKEN",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Rollback complete" in output
    assert str(rollback_journal) in output


def test_rollback_cli_interactive_confirmation_and_runtime_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args, prepared_apply, prepared_rollback = _rollback_paths(tmp_path)
    _patch_valid_boundary(monkeypatch, prepared_apply, prepared_rollback)
    rollback_journal = tmp_path / "rollback.jsonl"
    calls: list[bool] = []

    def execute(*_args: object, **kwargs: object) -> RollbackExecutionResult:
        calls.append(bool(kwargs["resume"]))
        return _result(check_only=False, journal=rollback_journal)

    monkeypatch.setattr(entrypoint, "execute_rollback", execute)
    monkeypatch.setattr(entrypoint, "total_rollback_members", lambda _prepared: 7)

    class Stdin:
        def __init__(self, interactive: bool):
            self.interactive = interactive

        def isatty(self) -> bool:
            return self.interactive

    monkeypatch.setattr(entrypoint.sys, "stdin", Stdin(False))
    assert (
        entrypoint.main([*args, "--rollback-journal", str(rollback_journal)])
        == entrypoint.ROLLBACK_FAILED_EXIT
    )
    assert "non-interactive rollback requires" in capsys.readouterr().err

    monkeypatch.setattr(entrypoint.sys, "stdin", Stdin(True))
    monkeypatch.setattr("builtins.input", lambda _prompt: " ROLLBACK-TOKEN ")
    assert (
        entrypoint.main(
            [
                *args,
                "--rollback-journal",
                str(rollback_journal),
                "--resume",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "atomically restore 7 files" in output
    assert "Confirmation token:\nROLLBACK-TOKEN" in output
    assert calls == [True]

    monkeypatch.setattr(
        entrypoint,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, True),
    )
    assert entrypoint.main([*args, "--check-only"]) == entrypoint.ROLLBACK_FAILED_EXIT
    assert "dirty source checkout" in capsys.readouterr().err


def test_rollback_cli_reports_interrupt_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args, _prepared_apply, _prepared_rollback = _rollback_paths(tmp_path)
    monkeypatch.setattr(
        entrypoint,
        "prepare_apply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    assert entrypoint.main([*args, "--check-only"]) == 130
    assert "Rollback interrupted" in capsys.readouterr().err
