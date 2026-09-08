from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from jellyfin_show_organizer import entrypoint, quarantine_cli
from jellyfin_show_organizer.apply_contract import ApplyContract
from jellyfin_show_organizer.apply_execution import PreparedApply
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.quarantine_contract import (
    QuarantineGroup,
    QuarantineMember,
    QuarantineMemberRole,
    QuarantinePlan,
    QuarantineWinner,
)
from jellyfin_show_organizer.quarantine_execution import (
    PreparedQuarantine,
    PreparedQuarantineRestore,
    QuarantineExecutionError,
    QuarantineExecutionResult,
    QuarantineRestoreResult,
)
from jellyfin_show_organizer.run_provenance import SourceRevision

pytestmark = pytest.mark.local


def _prepared() -> tuple[PreparedApply, PreparedQuarantine]:
    prepared_apply = PreparedApply(
        contract=ApplyContract(plan_sha256="a" * 64, groups=()),
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
    )
    fingerprint = SourceFingerprint(size=1, mtime_ns=1)
    plan = QuarantinePlan(
        schema_version=1,
        plan_sha256="a" * 64,
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
        groups=(
            QuarantineGroup(
                group_id="quarantine-one",
                duplicate_destination_key="show-s01e01",
                winner=QuarantineWinner(
                    source_relative_path="Show/winner.mkv",
                    organized_relative_path="Show (2026)/Season 01/winner.mkv",
                    fingerprint=fingerprint,
                ),
                members=(
                    QuarantineMember(
                        role=QuarantineMemberRole.VIDEO,
                        source_relative_path="Show/loser.mkv",
                        fingerprint=fingerprint,
                    ),
                ),
            ),
        ),
    )
    return prepared_apply, PreparedQuarantine(prepared_apply, plan)


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    organized = tmp_path / "organized"
    quarantine = tmp_path / "quarantine"
    for root in (source, organized, quarantine):
        root.mkdir(parents=True)
    return source, organized, quarantine


def _base_args(tmp_path: Path) -> list[str]:
    for name in ("plan.json", "preflight.json", "run-provenance.json", "qplan.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    source, organized, quarantine = _roots(tmp_path)
    return [
        str(tmp_path / "plan.json"),
        "--preflight",
        str(tmp_path / "preflight.json"),
        "--run-provenance",
        str(tmp_path / "run-provenance.json"),
        "--source-root",
        str(source),
        "--destination-root",
        str(organized),
        "--quarantine-root",
        str(quarantine),
        "--quarantine-plan",
        str(tmp_path / "qplan.json"),
        "--approve-plan-sha256",
        "a" * 64,
        "--approve-review-session-sha256",
        "b" * 64,
        "--approve-source-revision",
        "c" * 40,
        "--approve-quarantine-plan-sha256",
        "d" * 64,
    ]


def _quarantine_result(journal: Path) -> QuarantineExecutionResult:
    return QuarantineExecutionResult(
        quarantine_plan_sha256="d" * 64,
        plan_sha256="a" * 64,
        review_session_sha256="b" * 64,
        groups_total=1,
        members_total=1,
        winners_preapply=0,
        winners_organized=1,
        groups_completed=1,
        members_moved=1,
        members_recovered=0,
        journal_path=journal,
        check_only=False,
    )


def _restore_result(journal: Path) -> QuarantineRestoreResult:
    return QuarantineRestoreResult(
        quarantine_plan_sha256="d" * 64,
        quarantine_journal_sha256="e" * 64,
        groups_total=1,
        members_total=1,
        groups_completed=1,
        members_restored=1,
        members_recovered=0,
        journal_path=journal,
        check_only=False,
    )


def test_paths_and_prepared_requires_exact_clean_git_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared_apply, _ = _prepared()
    plan = tmp_path / "plan.json"
    preflight = tmp_path / "preflight.json"
    provenance = tmp_path / "run-provenance.json"
    for path in (plan, preflight, provenance):
        path.write_text("{}", encoding="utf-8")
    args = argparse.Namespace(
        plan=plan,
        preflight=preflight,
        run_provenance=provenance,
        approve_plan_sha256="A" * 64,
        approve_review_session_sha256="B" * 64,
        approve_source_revision="C" * 40,
    )
    observed: dict[str, str] = {}

    def fake_prepare_apply(
        _plan: Path,
        _preflight: Path,
        _provenance: Path,
        *,
        approved_plan_sha256: str,
        approved_review_session_sha256: str,
        approved_source_revision: str,
    ) -> PreparedApply:
        observed["plan"] = approved_plan_sha256
        observed["review"] = approved_review_session_sha256
        observed["revision"] = approved_source_revision
        return prepared_apply

    monkeypatch.setattr(quarantine_cli, "prepare_apply", fake_prepare_apply)
    monkeypatch.setattr(
        quarantine_cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, False),
    )
    assert quarantine_cli._paths_and_prepared(args)[3] == prepared_apply
    assert observed == {
        "plan": "a" * 64,
        "review": "b" * 64,
        "revision": "c" * 40,
    }

    monkeypatch.setattr(
        quarantine_cli,
        "detect_source_revision",
        lambda: SourceRevision("unavailable", None, None),
    )
    with pytest.raises(QuarantineExecutionError, match="verifiable clean Git"):
        quarantine_cli._paths_and_prepared(args)

    monkeypatch.setattr(
        quarantine_cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, True),
    )
    with pytest.raises(QuarantineExecutionError, match="dirty source checkout"):
        quarantine_cli._paths_and_prepared(args)

    monkeypatch.setattr(
        quarantine_cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "d" * 40, False),
    )
    with pytest.raises(QuarantineExecutionError, match="does not match"):
        quarantine_cli._paths_and_prepared(args)


def test_manifest_bytes_roots_and_prepare_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared_apply, prepared = _prepared()
    manifest_path = tmp_path / "plan.json"
    manifest_path.write_text('{"records":[]}', encoding="utf-8")
    assert quarantine_cli._load_manifest(manifest_path) == {"records": []}

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(QuarantineExecutionError, match="valid plan manifest"):
        quarantine_cli._load_manifest(invalid)

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    assert quarantine_cli._load_bytes(payload, "payload") == b"payload"
    with pytest.raises(QuarantineExecutionError, match="could not read missing"):
        quarantine_cli._load_bytes(tmp_path / "missing", "missing")

    source, organized, quarantine = _roots(tmp_path / "roots")
    args = argparse.Namespace(
        source_root=source,
        destination_root=organized,
        quarantine_root=quarantine,
    )
    assert quarantine_cli._roots(args) == (
        source.resolve(),
        organized.resolve(),
        quarantine.resolve(),
    )

    qplan = tmp_path / "qplan.json"
    qplan.write_bytes(b"synthetic-plan")
    prepare_args = argparse.Namespace(
        quarantine_plan=qplan,
        approve_quarantine_plan_sha256="D" * 64,
    )
    monkeypatch.setattr(
        quarantine_cli,
        "_paths_and_prepared",
        lambda _args: (manifest_path, tmp_path / "p", tmp_path / "r", prepared_apply),
    )
    monkeypatch.setattr(quarantine_cli, "_load_manifest", lambda _path: {"records": []})
    captured: dict[str, object] = {}

    def fake_prepare_quarantine(
        active_apply: PreparedApply,
        manifest: object,
        quarantine_plan_payload: bytes,
        *,
        approved_quarantine_plan_sha256: str,
    ) -> PreparedQuarantine:
        captured["apply"] = active_apply
        captured["manifest"] = manifest
        captured["payload"] = quarantine_plan_payload
        captured["sha"] = approved_quarantine_plan_sha256
        return prepared

    monkeypatch.setattr(quarantine_cli, "prepare_quarantine", fake_prepare_quarantine)
    assert quarantine_cli._prepare_quarantine_from_args(prepare_args) == prepared
    assert captured["payload"] == b"synthetic-plan"
    assert captured["sha"] == "d" * 64


def test_interactive_quarantine_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, prepared = _prepared()
    args = _base_args(tmp_path)
    journal = tmp_path / "quarantine.jsonl"
    monkeypatch.setattr(
        quarantine_cli, "_prepare_quarantine_from_args", lambda _args: prepared
    )
    monkeypatch.setattr(quarantine_cli, "quarantine_token", lambda *_args: "Q-TOKEN")
    monkeypatch.setattr(
        quarantine_cli,
        "execute_quarantine",
        lambda *_args, **_kwargs: _quarantine_result(journal),
    )

    class TTY:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(quarantine_cli.sys, "stdin", TTY())
    monkeypatch.setattr("builtins.input", lambda _prompt: "Q-TOKEN")
    assert entrypoint.main(["quarantine", *args, "--journal", str(journal)]) == 0
    output = capsys.readouterr().out
    assert "Exact quarantine approval required" in output
    assert "Confirmation token" in output


def test_interactive_restore_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, prepared = _prepared()
    args = _base_args(tmp_path)
    quarantine_journal = tmp_path / "quarantine.jsonl"
    quarantine_journal.write_text("synthetic", encoding="utf-8")
    restore_journal = tmp_path / "restore.jsonl"
    restore = PreparedQuarantineRestore(
        prepared=prepared,
        quarantine_journal_path=quarantine_journal,
        quarantine_journal_sha256="e" * 64,
    )
    monkeypatch.setattr(
        quarantine_cli, "_prepare_quarantine_from_args", lambda _args: prepared
    )
    monkeypatch.setattr(
        quarantine_cli, "prepare_quarantine_restore", lambda *_args: restore
    )
    monkeypatch.setattr(
        quarantine_cli, "quarantine_restore_token", lambda *_args: "R-TOKEN"
    )
    monkeypatch.setattr(
        quarantine_cli,
        "execute_quarantine_restore",
        lambda *_args, **_kwargs: _restore_result(restore_journal),
    )

    class TTY:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(quarantine_cli.sys, "stdin", TTY())
    monkeypatch.setattr("builtins.input", lambda _prompt: "R-TOKEN")
    assert (
        entrypoint.main(
            [
                "quarantine-restore",
                *args,
                "--quarantine-journal",
                str(quarantine_journal),
                "--restore-journal",
                str(restore_journal),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Exact quarantine restore approval required" in output
    assert "Confirmation token" in output
