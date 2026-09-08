from __future__ import annotations

import json
from pathlib import Path

import pytest

from jellyfin_show_organizer import rollback_execution
from jellyfin_show_organizer.apply_contract import (
    ApplyContract,
    ApplyMember,
    ApplyMemberRole,
    ApplyOperationGroup,
)
from jellyfin_show_organizer.apply_execution import PreparedApply, execute_apply
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.rollback_execution import (
    RollbackExecutionError,
    execute_rollback,
    prepare_rollback,
    rollback_token,
)

pytestmark = pytest.mark.local


def _fingerprint(path: Path) -> SourceFingerprint:
    stat = path.stat()
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    release = source_root / "Release"
    release.mkdir(parents=True)
    destination_root.mkdir()
    (release / "episode.mkv").write_bytes(b"synthetic-video")
    (release / "episode.en.srt").write_bytes(b"synthetic-subtitle")
    return source_root, destination_root


def _prepared(source_root: Path) -> tuple[PreparedApply, tuple[ApplyMember, ...]]:
    video = source_root / "Release" / "episode.mkv"
    sidecar = source_root / "Release" / "episode.en.srt"
    members = (
        ApplyMember(
            role=ApplyMemberRole.VIDEO,
            source_relative_path="Release/episode.mkv",
            destination_relative_path="Example (2026)/Season 01/episode.mkv",
            fingerprint=_fingerprint(video),
        ),
        ApplyMember(
            role=ApplyMemberRole.COMPANION,
            source_relative_path="Release/episode.en.srt",
            destination_relative_path="Example (2026)/Season 01/episode.en.srt",
            fingerprint=_fingerprint(sidecar),
        ),
    )
    return (
        PreparedApply(
            contract=ApplyContract(
                plan_sha256="a" * 64,
                groups=(ApplyOperationGroup(group_id="op-example", members=members),),
            ),
            review_session_sha256="b" * 64,
            source_revision="c" * 40,
        ),
        members,
    )


def _applied(
    tmp_path: Path,
) -> tuple[Path, Path, PreparedApply, tuple[ApplyMember, ...], Path]:
    source_root, destination_root = _roots(tmp_path)
    prepared, members = _prepared(source_root)
    apply_journal = tmp_path / "apply.jsonl"
    execute_apply(
        prepared,
        source_root,
        destination_root,
        journal_path=apply_journal,
    )
    return source_root, destination_root, prepared, members, apply_journal


def _path(root: Path, relative: str) -> Path:
    return root.joinpath(*relative.split("/"))


def test_completed_apply_can_be_checked_and_rolled_back_byte_for_byte(
    tmp_path: Path,
) -> None:
    source_root, destination_root, prepared, members, apply_journal = _applied(tmp_path)
    rollback = prepare_rollback(prepared, apply_journal)

    checked = execute_rollback(
        rollback,
        source_root,
        destination_root,
        rollback_journal_path=None,
        check_only=True,
    )
    assert checked.check_only
    assert checked.groups_total == 1
    assert checked.members_restored == 0

    rollback_journal = tmp_path / "rollback.jsonl"
    result = execute_rollback(
        rollback,
        source_root,
        destination_root,
        rollback_journal_path=rollback_journal,
    )

    assert result.groups_completed == 1
    assert result.members_restored == 2
    assert result.members_recovered == 0
    assert (
        _path(source_root, members[0].source_relative_path).read_bytes()
        == b"synthetic-video"
    )
    assert (
        _path(source_root, members[1].source_relative_path).read_bytes()
        == b"synthetic-subtitle"
    )
    for member in members:
        assert not _path(destination_root, member.destination_relative_path).exists()
    assert (destination_root / "Example (2026)" / "Season 01").is_dir()
    events = [
        json.loads(line)["event"] for line in rollback_journal.read_text().splitlines()
    ]
    assert events[0] == "rollback-started"
    assert events[-1] == "rollback-completed"
    assert events.count("member-completed") == 2


def test_rollback_resume_recovers_move_between_started_and_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, destination_root, prepared, members, apply_journal = _applied(tmp_path)
    rollback = prepare_rollback(prepared, apply_journal)
    rollback_journal = tmp_path / "rollback.jsonl"
    original_append = rollback_execution._RollbackJournal.append
    failed = False

    def fail_first_completion(self, event: str, **kwargs) -> None:
        nonlocal failed
        if event == "member-completed" and not failed:
            failed = True
            raise RollbackExecutionError("synthetic completion journal failure")
        original_append(self, event, **kwargs)

    monkeypatch.setattr(
        rollback_execution._RollbackJournal,
        "append",
        fail_first_completion,
    )
    with pytest.raises(RollbackExecutionError, match="completion journal failure"):
        execute_rollback(
            rollback,
            source_root,
            destination_root,
            rollback_journal_path=rollback_journal,
        )

    monkeypatch.setattr(
        rollback_execution._RollbackJournal,
        "append",
        original_append,
    )
    resumed = execute_rollback(
        rollback,
        source_root,
        destination_root,
        rollback_journal_path=rollback_journal,
        resume=True,
    )

    assert resumed.groups_completed == 1
    assert resumed.members_recovered == 1
    for member in members:
        assert _path(source_root, member.source_relative_path).is_file()
        assert not _path(destination_root, member.destination_relative_path).exists()


def test_changed_destination_blocks_rollback_before_mutation(tmp_path: Path) -> None:
    source_root, destination_root, prepared, members, apply_journal = _applied(tmp_path)
    target = _path(destination_root, members[0].destination_relative_path)
    target.write_bytes(b"changed-destination")
    rollback = prepare_rollback(prepared, apply_journal)

    with pytest.raises(RollbackExecutionError, match="destination does not match"):
        execute_rollback(
            rollback,
            source_root,
            destination_root,
            rollback_journal_path=None,
            check_only=True,
        )

    assert target.read_bytes() == b"changed-destination"
    assert not _path(source_root, members[0].source_relative_path).exists()


def test_recreated_source_collision_blocks_rollback(tmp_path: Path) -> None:
    source_root, destination_root, prepared, members, apply_journal = _applied(tmp_path)
    source = _path(source_root, members[0].source_relative_path)
    source.write_bytes(b"new-file")
    rollback = prepare_rollback(prepared, apply_journal)

    with pytest.raises(RollbackExecutionError, match="both the original source"):
        execute_rollback(
            rollback,
            source_root,
            destination_root,
            rollback_journal_path=None,
            check_only=True,
        )

    assert source.read_bytes() == b"new-file"
    assert _path(destination_root, members[0].destination_relative_path).is_file()


def test_missing_destination_blocks_rollback(tmp_path: Path) -> None:
    source_root, destination_root, prepared, members, apply_journal = _applied(tmp_path)
    _path(destination_root, members[0].destination_relative_path).unlink()
    rollback = prepare_rollback(prepared, apply_journal)

    with pytest.raises(
        RollbackExecutionError, match="both the original source.*missing"
    ):
        execute_rollback(
            rollback,
            source_root,
            destination_root,
            rollback_journal_path=None,
            check_only=True,
        )


def test_tampered_apply_journal_is_rejected(tmp_path: Path) -> None:
    _source_root, _destination_root, prepared, _members, apply_journal = _applied(
        tmp_path
    )
    lines = apply_journal.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["plan_sha256"] = "d" * 64
    lines[0] = json.dumps(first)
    apply_journal.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(RollbackExecutionError, match="another plan"):
        prepare_rollback(prepared, apply_journal)


def test_tampered_rollback_journal_is_rejected_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, destination_root, prepared, _members, apply_journal = _applied(
        tmp_path
    )
    rollback = prepare_rollback(prepared, apply_journal)
    rollback_journal = tmp_path / "rollback.jsonl"
    original_rename = rollback_execution._atomic_rename_no_replace
    calls = 0

    def interrupt_second(destination: Path, source: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        original_rename(destination, source)

    monkeypatch.setattr(
        rollback_execution,
        "_atomic_rename_no_replace",
        interrupt_second,
    )
    with pytest.raises(RollbackExecutionError, match="interrupted safely"):
        execute_rollback(
            rollback,
            source_root,
            destination_root,
            rollback_journal_path=rollback_journal,
        )

    lines = rollback_journal.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["apply_journal_sha256"] = "e" * 64
    lines[0] = json.dumps(first)
    rollback_journal.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        rollback_execution,
        "_atomic_rename_no_replace",
        original_rename,
    )
    with pytest.raises(RollbackExecutionError, match="another apply journal"):
        execute_rollback(
            rollback,
            source_root,
            destination_root,
            rollback_journal_path=rollback_journal,
            resume=True,
        )


def test_rollback_token_binds_apply_journal_and_roots(tmp_path: Path) -> None:
    source_root, destination_root, prepared, _members, apply_journal = _applied(
        tmp_path
    )
    rollback = prepare_rollback(prepared, apply_journal)
    token = rollback_token(rollback, source_root, destination_root)
    assert token.startswith("ROLLBACK:" + "a" * 64 + ":" + "b" * 64)
    assert rollback.apply_journal_sha256 in token

    other_destination = tmp_path / "other-destination"
    other_destination.mkdir()
    assert token != rollback_token(rollback, source_root, other_destination)
