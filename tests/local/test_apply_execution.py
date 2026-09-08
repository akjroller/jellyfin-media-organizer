from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jellyfin_show_organizer import apply_execution
from jellyfin_show_organizer.apply_contract import (
    ApplyContract,
    ApplyMember,
    ApplyMemberRole,
    ApplyOperationGroup,
)
from jellyfin_show_organizer.apply_execution import (
    ApplyExecutionError,
    PreparedApply,
    approval_token,
    execute_apply,
)
from jellyfin_show_organizer.models import SourceFingerprint

pytestmark = pytest.mark.local


def _fingerprint(path: Path) -> SourceFingerprint:
    stat = path.stat()
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _prepared(
    source_root: Path,
    *,
    companion: bool = True,
) -> tuple[PreparedApply, tuple[ApplyMember, ...]]:
    video_path = source_root / "Release" / "episode.mkv"
    members = [
        ApplyMember(
            role=ApplyMemberRole.VIDEO,
            source_relative_path="Release/episode.mkv",
            destination_relative_path="Example (2026)/Season 01/episode.mkv",
            fingerprint=_fingerprint(video_path),
        )
    ]
    if companion:
        sidecar_path = source_root / "Release" / "episode.en.srt"
        members.append(
            ApplyMember(
                role=ApplyMemberRole.COMPANION,
                source_relative_path="Release/episode.en.srt",
                destination_relative_path=("Example (2026)/Season 01/episode.en.srt"),
                fingerprint=_fingerprint(sidecar_path),
            )
        )
    contract = ApplyContract(
        plan_sha256="a" * 64,
        groups=(ApplyOperationGroup(group_id="op-example", members=tuple(members)),),
    )
    return (
        PreparedApply(
            contract=contract,
            review_session_sha256="b" * 64,
            source_revision="c" * 40,
        ),
        tuple(members),
    )


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    release = source_root / "Release"
    release.mkdir(parents=True)
    destination_root.mkdir()
    (release / "episode.mkv").write_bytes(b"synthetic-video")
    (release / "episode.en.srt").write_bytes(b"synthetic-subtitle")
    return source_root, destination_root


def test_check_only_revalidates_without_mutating_or_creating_journal(tmp_path: Path):
    source_root, destination_root = _roots(tmp_path)
    prepared, _members = _prepared(source_root)
    journal = tmp_path / "apply.jsonl"

    result = execute_apply(
        prepared,
        source_root,
        destination_root,
        journal_path=journal,
        check_only=True,
    )

    assert result.check_only
    assert result.groups_total == 1
    assert result.members_moved == 0
    assert (source_root / "Release" / "episode.mkv").is_file()
    assert list(destination_root.iterdir()) == []
    assert not journal.exists()


def test_apply_moves_group_and_persists_complete_journal(tmp_path: Path):
    source_root, destination_root = _roots(tmp_path)
    prepared, members = _prepared(source_root)
    journal = tmp_path / "apply.jsonl"

    result = execute_apply(
        prepared,
        source_root,
        destination_root,
        journal_path=journal,
    )

    assert result.groups_completed == 1
    assert result.members_moved == 2
    for member in members:
        assert not source_root.joinpath(
            *member.source_relative_path.split("/")
        ).exists()
        assert destination_root.joinpath(
            *member.destination_relative_path.split("/")
        ).is_file()
    events = [json.loads(line)["event"] for line in journal.read_text().splitlines()]
    assert events[0] == "run-started"
    assert events[-1] == "run-completed"
    assert events.count("member-completed") == 2
    assert (tmp_path / "apply.jsonl.lock").is_file()


def test_destination_race_fails_before_any_move(tmp_path: Path):
    source_root, destination_root = _roots(tmp_path)
    prepared, _members = _prepared(source_root)
    target = destination_root / "Example (2026)" / "Season 01" / "episode.mkv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing")

    with pytest.raises(ApplyExecutionError, match="destination already exists"):
        execute_apply(
            prepared,
            source_root,
            destination_root,
            journal_path=tmp_path / "apply.jsonl",
        )

    assert (source_root / "Release" / "episode.mkv").is_file()
    assert target.read_bytes() == b"existing"


def test_later_member_failure_rolls_back_completed_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source_root, destination_root = _roots(tmp_path)
    prepared, members = _prepared(source_root)
    actual_rename = apply_execution._atomic_rename_no_replace

    def fail_sidecar(source: Path, destination: Path) -> None:
        if source.suffix == ".srt":
            raise PermissionError("synthetic sidecar denial")
        actual_rename(source, destination)

    monkeypatch.setattr(apply_execution, "_atomic_rename_no_replace", fail_sidecar)

    with pytest.raises(ApplyExecutionError, match="rolled back safely"):
        execute_apply(
            prepared,
            source_root,
            destination_root,
            journal_path=tmp_path / "apply.jsonl",
        )

    for member in members:
        assert source_root.joinpath(*member.source_relative_path.split("/")).is_file()
        assert not destination_root.joinpath(
            *member.destination_relative_path.split("/")
        ).exists()
    events = [
        json.loads(line)["event"]
        for line in (tmp_path / "apply.jsonl").read_text().splitlines()
    ]
    assert "member-rollback-completed" in events
    assert events[-1] == "group-failed"


def test_journal_failure_after_move_still_restores_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source_root, destination_root = _roots(tmp_path)
    prepared, (member,) = _prepared(source_root, companion=False)
    original_append = apply_execution._Journal.append

    def fail_after_move(self, event: str, **kwargs) -> None:
        if event in {
            "member-completed",
            "member-rollback-started",
            "member-rollback-completed",
            "group-failed",
        }:
            raise ApplyExecutionError("synthetic journal failure")
        original_append(self, event, **kwargs)

    monkeypatch.setattr(apply_execution._Journal, "append", fail_after_move)

    with pytest.raises(ApplyExecutionError, match="journal failure"):
        execute_apply(
            prepared,
            source_root,
            destination_root,
            journal_path=tmp_path / "apply.jsonl",
        )

    source = source_root.joinpath(*member.source_relative_path.split("/"))
    destination = destination_root.joinpath(
        *member.destination_relative_path.split("/")
    )
    assert source.read_bytes() == b"synthetic-video"
    assert not destination.exists()


def test_resume_adopts_move_completed_between_journal_events(tmp_path: Path):
    source_root, destination_root = _roots(tmp_path)
    prepared, (member,) = _prepared(source_root, companion=False)
    journal_path = tmp_path / "apply.jsonl"
    journal = apply_execution._Journal(journal_path, prepared, resume=False)
    journal.append("run-started", result="started")
    journal.append("group-started", group_id="op-example", result="started")
    destination = destination_root.joinpath(
        *member.destination_relative_path.split("/")
    )
    destination.parent.mkdir(parents=True)
    source = source_root.joinpath(*member.source_relative_path.split("/"))
    journal.append(
        "member-started",
        group_id="op-example",
        member=member,
        result="started",
    )
    os.rename(source, destination)

    result = execute_apply(
        prepared,
        source_root,
        destination_root,
        journal_path=journal_path,
        resume=True,
    )

    assert result.groups_completed == 1
    assert result.members_moved == 0
    assert result.members_recovered == 1
    assert not source.exists()
    assert destination.is_file()
    entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
    assert any(
        entry["event"] == "member-completed"
        and entry["result"] == "recovered-after-interruption"
        for entry in entries
    )


def test_existing_lock_blocks_concurrent_apply(tmp_path: Path):
    source_root, destination_root = _roots(tmp_path)
    prepared, _members = _prepared(source_root)
    journal = tmp_path / "apply.jsonl"
    lock = apply_execution._journal_lock(journal)
    try:
        with pytest.raises(ApplyExecutionError, match="lock already exists"):
            execute_apply(
                prepared,
                source_root,
                destination_root,
                journal_path=journal,
            )
    finally:
        apply_execution._release_journal_lock(lock)

    assert (source_root / "Release" / "episode.mkv").is_file()
    assert not journal.exists()


def test_confirmation_token_binds_both_roots(tmp_path: Path):
    source_root, destination_root = _roots(tmp_path)
    other_destination = tmp_path / "other-destination"
    other_destination.mkdir()
    prepared, _members = _prepared(source_root)

    first = approval_token(prepared, source_root, destination_root)
    second = approval_token(prepared, source_root, other_destination)

    assert first.startswith("APPLY:" + "a" * 64 + ":" + "b" * 64)
    assert first != second


def test_resume_completed_run_only_verifies_destinations(tmp_path: Path):
    source_root, destination_root = _roots(tmp_path)
    prepared, _members = _prepared(source_root)
    journal = tmp_path / "apply.jsonl"
    execute_apply(
        prepared,
        source_root,
        destination_root,
        journal_path=journal,
    )

    resumed = execute_apply(
        prepared,
        source_root,
        destination_root,
        journal_path=journal,
        resume=True,
    )

    assert resumed.groups_completed == 1
    assert resumed.members_moved == 0
    assert resumed.members_recovered == 0


def test_apply_refuses_invalid_journal_modes_and_locations(tmp_path: Path):
    source_root, destination_root = _roots(tmp_path)
    prepared, _members = _prepared(source_root)

    with pytest.raises(ApplyExecutionError, match="check-only.*resume"):
        execute_apply(
            prepared,
            source_root,
            destination_root,
            journal_path=None,
            check_only=True,
            resume=True,
        )
    with pytest.raises(ApplyExecutionError, match="explicit journal"):
        execute_apply(prepared, source_root, destination_root, journal_path=None)
    with pytest.raises(ApplyExecutionError, match="outside the source"):
        execute_apply(
            prepared,
            source_root,
            destination_root,
            journal_path=source_root / "apply.jsonl",
        )
    with pytest.raises(ApplyExecutionError, match="outside the destination"):
        execute_apply(
            prepared,
            source_root,
            destination_root,
            journal_path=destination_root / "apply.jsonl",
        )


def test_new_journal_refuses_existing_file_and_resume_requires_one(tmp_path: Path):
    source_root, _destination_root = _roots(tmp_path)
    prepared, _members = _prepared(source_root)
    path = tmp_path / "apply.jsonl"

    with pytest.raises(ApplyExecutionError, match="does not exist"):
        apply_execution._Journal(path, prepared, resume=True)
    path.write_text("", encoding="utf-8")
    with pytest.raises(ApplyExecutionError, match="already exists"):
        apply_execution._Journal(path, prepared, resume=False)
    with pytest.raises(ApplyExecutionError, match="empty"):
        apply_execution._Journal(path, prepared, resume=True)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 999, "schema"),
        ("sequence", 2, "sequence"),
        ("event", "unknown", "unknown event"),
        ("plan_sha256", "d" * 64, "another plan"),
        ("review_session_sha256", "d" * 64, "another review"),
        ("source_revision", "d" * 40, "another revision"),
    ],
)
def test_resume_journal_rejects_changed_identity_fields(
    tmp_path: Path, field: str, value: object, message: str
):
    source_root, _destination_root = _roots(tmp_path)
    prepared, _members = _prepared(source_root)
    path = tmp_path / "apply.jsonl"
    journal = apply_execution._Journal(path, prepared, resume=False)
    journal.append("run-started", result="started")
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry[field] = value
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    with pytest.raises(ApplyExecutionError, match=message):
        apply_execution._Journal(path, prepared, resume=True)


def test_resume_journal_rejects_invalid_json(tmp_path: Path):
    source_root, _destination_root = _roots(tmp_path)
    prepared, _members = _prepared(source_root)
    path = tmp_path / "apply.jsonl"
    path.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(ApplyExecutionError, match="invalid JSON"):
        apply_execution._Journal(path, prepared, resume=True)
