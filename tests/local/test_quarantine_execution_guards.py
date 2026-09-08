from __future__ import annotations

import json
from pathlib import Path

import pytest

from jellyfin_show_organizer import quarantine_execution
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
    QuarantineExecutionError,
    execute_quarantine,
    execute_quarantine_restore,
    prepare_quarantine_restore,
    quarantine_restore_token,
    quarantine_token,
)

pytestmark = pytest.mark.local


def _fingerprint(path: Path) -> SourceFingerprint:
    stat = path.stat()
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _case(tmp_path: Path) -> tuple[Path, Path, Path, PreparedQuarantine]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source"
    organized = tmp_path / "organized"
    quarantine = tmp_path / "quarantine"
    source.mkdir()
    organized.mkdir()
    quarantine.mkdir()
    release = source / "Release"
    release.mkdir()
    winner_path = release / "winner.mkv"
    loser_path = release / "loser.mkv"
    winner_path.write_bytes(b"winner")
    loser_path.write_bytes(b"loser")
    prepared_apply = PreparedApply(
        contract=ApplyContract(plan_sha256="a" * 64, groups=()),
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
    )
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
                    source_relative_path="Release/winner.mkv",
                    organized_relative_path="Show (2026)/Season 01/winner.mkv",
                    fingerprint=_fingerprint(winner_path),
                ),
                members=(
                    QuarantineMember(
                        role=QuarantineMemberRole.VIDEO,
                        source_relative_path="Release/loser.mkv",
                        fingerprint=_fingerprint(loser_path),
                    ),
                ),
            ),
        ),
    )
    return source, organized, quarantine, PreparedQuarantine(prepared_apply, plan)


def _organize_winner(source: Path, organized: Path) -> None:
    source_path = source / "Release" / "winner.mkv"
    destination = organized / "Show (2026)" / "Season 01" / "winner.mkv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_path.rename(destination)


def _complete_quarantine(
    tmp_path: Path,
) -> tuple[Path, Path, Path, PreparedQuarantine, Path]:
    source, organized, quarantine, prepared = _case(tmp_path)
    _organize_winner(source, organized)
    journal = tmp_path / "quarantine.jsonl"
    execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=journal,
    )
    return source, organized, quarantine, prepared, journal


def test_root_and_journal_guards_fail_closed(tmp_path: Path) -> None:
    source, organized, quarantine, prepared = _case(tmp_path)
    with pytest.raises(QuarantineExecutionError, match="root does not exist"):
        execute_quarantine(
            prepared,
            source,
            organized,
            tmp_path / "missing",
            journal_path=None,
            check_only=True,
        )

    file_root = tmp_path / "file-root"
    file_root.write_text("x", encoding="utf-8")
    with pytest.raises(QuarantineExecutionError, match="must be a directory"):
        quarantine_execution._validated_root(file_root, "test")

    nested_source = source / "nested"
    nested_source.mkdir()
    with pytest.raises(QuarantineExecutionError, match="outside the source root"):
        execute_quarantine(
            prepared,
            source,
            organized,
            nested_source,
            journal_path=None,
            check_only=True,
        )

    nested_organized = organized / "nested"
    nested_organized.mkdir()
    with pytest.raises(QuarantineExecutionError, match="outside the organized"):
        execute_quarantine(
            prepared,
            source,
            organized,
            nested_organized,
            journal_path=None,
            check_only=True,
        )

    with pytest.raises(QuarantineExecutionError, match="outside all media roots"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=source / "journal.jsonl",
        )


def test_candidate_path_and_mapping_guards(tmp_path: Path) -> None:
    source, _organized, _quarantine, _prepared = _case(tmp_path)
    with pytest.raises(
        QuarantineExecutionError, match="required member parent is missing"
    ):
        quarantine_execution._candidate_path(
            source, "Missing/file.mkv", require_parent=True
        )

    bad_parent = source / "Bad"
    bad_parent.write_text("not-a-directory", encoding="utf-8")
    with pytest.raises(QuarantineExecutionError, match="link or non-directory"):
        quarantine_execution._candidate_path(
            source, "Bad/file.mkv", require_parent=False
        )

    with pytest.raises(QuarantineExecutionError, match="must be an object"):
        quarantine_execution._mapping([], "synthetic")


def test_winner_ambiguities_are_rejected(tmp_path: Path) -> None:
    source, organized, quarantine, prepared = _case(tmp_path / "both")
    organized_winner = organized / "Show (2026)" / "Season 01" / "winner.mkv"
    organized_winner.parent.mkdir(parents=True)
    organized_winner.write_bytes(b"winner")
    with pytest.raises(QuarantineExecutionError, match="both original and organized"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
        )

    source, organized, quarantine, prepared = _case(tmp_path / "bad-source")
    (source / "Release" / "winner.mkv").write_bytes(b"changed")
    with pytest.raises(QuarantineExecutionError, match="source fingerprint is wrong"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
        )

    source, organized, quarantine, prepared = _case(tmp_path / "bad-organized")
    (source / "Release" / "winner.mkv").unlink()
    organized_winner = organized / "Show (2026)" / "Season 01" / "winner.mkv"
    organized_winner.parent.mkdir(parents=True)
    organized_winner.write_bytes(b"wrong")
    with pytest.raises(
        QuarantineExecutionError,
        match="organized destination fingerprint is wrong",
    ):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
        )


def test_loser_ambiguities_are_rejected(tmp_path: Path) -> None:
    source, organized, quarantine, prepared = _case(tmp_path / "missing")
    (source / "Release" / "loser.mkv").unlink()
    with pytest.raises(QuarantineExecutionError, match="both source and quarantine"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
        )

    source, organized, quarantine, prepared = _case(tmp_path / "bad-quarantine")
    (source / "Release" / "loser.mkv").unlink()
    quarantined = quarantine / "Release" / "loser.mkv"
    quarantined.parent.mkdir()
    quarantined.write_bytes(b"wrong")
    with pytest.raises(QuarantineExecutionError, match="fingerprint is invalid"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
        )


def test_quarantine_flow_guards_and_completed_reentry(tmp_path: Path) -> None:
    source, organized, quarantine, prepared = _case(tmp_path)
    with pytest.raises(QuarantineExecutionError, match="cannot be combined"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
            resume=True,
        )
    with pytest.raises(QuarantineExecutionError, match="explicit journal"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
        )

    _organize_winner(source, organized)
    journal = tmp_path / "quarantine.jsonl"
    first = execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=journal,
    )
    assert first.members_moved == 1
    again = execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=journal,
        resume=True,
    )
    assert again.groups_completed == 1
    assert again.members_moved == 0

    source2, organized2, quarantine2, prepared2 = _case(tmp_path / "other")
    _organize_winner(source2, organized2)
    existing = tmp_path / "occupied.jsonl"
    existing.write_text("occupied", encoding="utf-8")
    with pytest.raises(QuarantineExecutionError, match="already exists"):
        execute_quarantine(
            prepared2,
            source2,
            organized2,
            quarantine2,
            journal_path=existing,
        )
    with pytest.raises(QuarantineExecutionError, match="resume quarantine journal"):
        execute_quarantine(
            prepared2,
            source2,
            organized2,
            quarantine2,
            journal_path=tmp_path / "missing-resume.jsonl",
            resume=True,
        )


def _tamper(line: str, field: str, value: object) -> str:
    payload = json.loads(line)
    payload[field] = value
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 99, "unsupported quarantine journal schema"),
        ("sequence", 7, "sequence is not contiguous"),
        ("event", "delete-everything", "unknown event"),
        ("quarantine_plan_sha256", "f" * 64, "another quarantine plan"),
        ("plan_sha256", "f" * 64, "another plan"),
        ("review_session_sha256", "f" * 64, "another review"),
        ("source_revision", "f" * 40, "another revision"),
    ],
)
def test_tampered_quarantine_journal_fields_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    _source, _organized, _quarantine, prepared, journal = _complete_quarantine(
        tmp_path
    )
    lines = journal.read_text(encoding="utf-8").splitlines()
    lines[0] = _tamper(lines[0], field, value)
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(QuarantineExecutionError, match=message):
        quarantine_execution._QuarantineJournal(tampered, prepared, resume=True)


def test_invalid_empty_and_incomplete_quarantine_journals_are_rejected(
    tmp_path: Path,
) -> None:
    _source, _organized, _quarantine, prepared = _case(tmp_path)
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(QuarantineExecutionError, match="invalid JSON"):
        quarantine_execution._QuarantineJournal(invalid, prepared, resume=True)

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(QuarantineExecutionError, match="is empty"):
        quarantine_execution._QuarantineJournal(empty, prepared, resume=True)

    incomplete = tmp_path / "incomplete.jsonl"
    journal = quarantine_execution._QuarantineJournal(
        incomplete, prepared, resume=False
    )
    journal.append("run-started", result="started")
    with pytest.raises(QuarantineExecutionError, match="completed quarantine run"):
        prepare_quarantine_restore(prepared, incomplete)

    with pytest.raises(QuarantineExecutionError, match="does not exist"):
        prepare_quarantine_restore(prepared, tmp_path / "missing.jsonl")


def test_restore_guards_tamper_and_completed_reentry(tmp_path: Path) -> None:
    source, organized, quarantine, prepared, journal = _complete_quarantine(tmp_path)
    restore = prepare_quarantine_restore(prepared, journal)

    with pytest.raises(QuarantineExecutionError, match="cannot be combined"):
        execute_quarantine_restore(
            restore,
            source,
            organized,
            quarantine,
            restore_journal_path=None,
            check_only=True,
            resume=True,
        )
    with pytest.raises(QuarantineExecutionError, match="explicit restore journal"):
        execute_quarantine_restore(
            restore,
            source,
            organized,
            quarantine,
            restore_journal_path=None,
        )

    restore_journal = tmp_path / "restore.jsonl"
    first = execute_quarantine_restore(
        restore,
        source,
        organized,
        quarantine,
        restore_journal_path=restore_journal,
    )
    assert first.members_restored == 1
    again = execute_quarantine_restore(
        restore,
        source,
        organized,
        quarantine,
        restore_journal_path=restore_journal,
        resume=True,
    )
    assert again.groups_completed == 1
    assert again.members_restored == 0

    journal.write_bytes(journal.read_bytes() + b" \n")
    with pytest.raises(QuarantineExecutionError, match="changed after restore approval"):
        execute_quarantine_restore(
            restore,
            source,
            organized,
            quarantine,
            restore_journal_path=tmp_path / "tampered-restore.jsonl",
        )


def test_confirmation_tokens_bind_roots_and_journal(tmp_path: Path) -> None:
    source, organized, quarantine, prepared = _case(tmp_path)
    token = quarantine_token(prepared, source, organized, quarantine)
    assert token.startswith("QUARANTINE:")

    other = tmp_path / "other-quarantine"
    other.mkdir()
    assert token != quarantine_token(prepared, source, organized, other)

    _organize_winner(source, organized)
    journal = tmp_path / "quarantine.jsonl"
    execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=journal,
    )
    restore = prepare_quarantine_restore(prepared, journal)
    restore_token = quarantine_restore_token(restore, source, organized, quarantine)
    assert restore_token.startswith("QUARANTINE-RESTORE:")
    assert restore.quarantine_journal_sha256 in restore_token


def test_journal_state_tracks_completion_and_rollback() -> None:
    entries = [
        {
            "event": "member-started",
            "group_id": "g",
            "source_relative_path": "A.mkv",
        },
        {
            "event": "member-completed",
            "group_id": "g",
            "source_relative_path": "A.mkv",
        },
        {
            "event": "member-rollback-completed",
            "group_id": "g",
            "source_relative_path": "A.mkv",
        },
        {
            "event": "group-completed",
            "group_id": "g",
            "source_relative_path": None,
        },
        {
            "event": "run-completed",
            "group_id": None,
            "source_relative_path": None,
        },
    ]
    state = quarantine_execution._journal_state(
        entries, completion_event="run-completed"
    )
    assert state.completed_groups == frozenset({"g"})
    assert state.completed_members == frozenset()
    assert state.started_members == frozenset()
    assert state.run_completed
