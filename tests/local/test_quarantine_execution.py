from __future__ import annotations

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
)

pytestmark = pytest.mark.local


def _fingerprint(path: Path) -> SourceFingerprint:
    stat = path.stat()
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _write(root: Path, relative: str, payload: bytes) -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, PreparedQuarantine]:
    source = tmp_path / "source"
    organized = tmp_path / "organized"
    quarantine = tmp_path / "quarantine"
    source.mkdir()
    organized.mkdir()
    quarantine.mkdir()

    winner1_path = _write(source, "Show/release-winner.mkv", b"winner-one")
    loser1_path = _write(source, "Show/release-loser-a.mkv", b"loser-one")
    loser1_sub = _write(source, "Show/release-loser-a.en.srt", b"subtitle-one")
    loser2_path = _write(source, "Show/release-loser-b.mkv", b"loser-two")
    winner2_path = _write(source, "Other/winner.mkv", b"winner-two")
    loser3_path = _write(source, "Other/loser.mkv", b"loser-three")

    winner1 = QuarantineWinner(
        source_relative_path="Show/release-winner.mkv",
        organized_relative_path="Show (2026)/Season 01/Show S01E01.mkv",
        fingerprint=_fingerprint(winner1_path),
    )
    winner2 = QuarantineWinner(
        source_relative_path="Other/winner.mkv",
        organized_relative_path="Other (2026)/Season 01/Other S01E01.mkv",
        fingerprint=_fingerprint(winner2_path),
    )
    groups = (
        QuarantineGroup(
            group_id="quarantine-a",
            duplicate_destination_key="show-s01e01",
            winner=winner1,
            members=(
                QuarantineMember(
                    role=QuarantineMemberRole.VIDEO,
                    source_relative_path="Show/release-loser-a.mkv",
                    fingerprint=_fingerprint(loser1_path),
                ),
                QuarantineMember(
                    role=QuarantineMemberRole.COMPANION,
                    source_relative_path="Show/release-loser-a.en.srt",
                    fingerprint=_fingerprint(loser1_sub),
                ),
            ),
        ),
        QuarantineGroup(
            group_id="quarantine-b",
            duplicate_destination_key="show-s01e01",
            winner=winner1,
            members=(
                QuarantineMember(
                    role=QuarantineMemberRole.VIDEO,
                    source_relative_path="Show/release-loser-b.mkv",
                    fingerprint=_fingerprint(loser2_path),
                ),
            ),
        ),
        QuarantineGroup(
            group_id="quarantine-c",
            duplicate_destination_key="other-s01e01",
            winner=winner2,
            members=(
                QuarantineMember(
                    role=QuarantineMemberRole.VIDEO,
                    source_relative_path="Other/loser.mkv",
                    fingerprint=_fingerprint(loser3_path),
                ),
            ),
        ),
    )
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
        groups=groups,
    )
    return source, organized, quarantine, PreparedQuarantine(prepared_apply, plan)


def _organize_winners(
    source: Path, organized: Path, prepared: PreparedQuarantine
) -> None:
    seen: set[str] = set()
    for group in prepared.plan.groups:
        relative = group.winner.source_relative_path
        if relative in seen:
            continue
        seen.add(relative)
        src = source.joinpath(*relative.split("/"))
        dst = organized.joinpath(*group.winner.organized_relative_path.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


def test_check_only_supports_preapply_then_requires_organized_winners_for_mutation(
    tmp_path: Path,
) -> None:
    source, organized, quarantine, prepared = _fixture(tmp_path)

    checked = execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=None,
        check_only=True,
    )
    assert checked.winners_preapply == 3
    assert checked.winners_organized == 0

    with pytest.raises(QuarantineExecutionError, match="every reviewed winner"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=tmp_path / "too-early.jsonl",
        )

    _organize_winners(source, organized, prepared)
    checked = execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=None,
        check_only=True,
    )
    assert checked.winners_preapply == 0
    assert checked.winners_organized == 3


def test_complete_quarantine_and_restore_is_byte_for_byte_reversible(
    tmp_path: Path,
) -> None:
    source, organized, quarantine, prepared = _fixture(tmp_path)
    originals = {
        member.source_relative_path: source.joinpath(
            *member.source_relative_path.split("/")
        ).read_bytes()
        for group in prepared.plan.groups
        for member in group.members
    }
    _organize_winners(source, organized, prepared)
    journal = tmp_path / "quarantine.jsonl"

    result = execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=journal,
    )
    assert result.groups_completed == 3
    assert result.members_moved == 4
    for relative in originals:
        assert not source.joinpath(*relative.split("/")).exists()
        assert (
            quarantine.joinpath(*relative.split("/")).read_bytes()
            == originals[relative]
        )

    restore = prepare_quarantine_restore(prepared, journal)
    checked = execute_quarantine_restore(
        restore,
        source,
        organized,
        quarantine,
        restore_journal_path=None,
        check_only=True,
    )
    assert checked.members_total == 4

    restored = execute_quarantine_restore(
        restore,
        source,
        organized,
        quarantine,
        restore_journal_path=tmp_path / "restore.jsonl",
    )
    assert restored.groups_completed == 3
    assert restored.members_restored == 4
    for relative, payload in originals.items():
        assert source.joinpath(*relative.split("/")).read_bytes() == payload
        assert not quarantine.joinpath(*relative.split("/")).exists()


def test_missing_winner_changed_loser_and_quarantine_collision_fail_closed(
    tmp_path: Path,
) -> None:
    source, organized, quarantine, prepared = _fixture(tmp_path)
    source.joinpath("Show", "release-winner.mkv").unlink()
    with pytest.raises(QuarantineExecutionError, match="winner is missing"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
        )

    source, organized, quarantine, prepared = _fixture(tmp_path / "changed")
    source.joinpath("Show", "release-loser-a.mkv").write_bytes(b"changed loser")
    with pytest.raises(QuarantineExecutionError, match="fingerprint"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
        )

    source, organized, quarantine, prepared = _fixture(tmp_path / "collision")
    _write(quarantine, "Show/release-loser-a.mkv", b"occupied")
    with pytest.raises(QuarantineExecutionError, match="both source and quarantine"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=None,
            check_only=True,
        )


def test_changed_companion_blocks_group_before_video_moves(tmp_path: Path) -> None:
    source, organized, quarantine, prepared = _fixture(tmp_path)
    _organize_winners(source, organized, prepared)
    companion = source.joinpath("Show", "release-loser-a.en.srt")
    companion.write_bytes(b"changed companion")

    with pytest.raises(QuarantineExecutionError, match="fingerprint"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=tmp_path / "quarantine.jsonl",
        )

    assert source.joinpath("Show", "release-loser-a.mkv").is_file()
    assert not quarantine.joinpath("Show", "release-loser-a.mkv").exists()


def test_quarantine_crash_after_atomic_move_is_adopted_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, organized, quarantine, prepared = _fixture(tmp_path)
    _organize_winners(source, organized, prepared)
    journal = tmp_path / "quarantine.jsonl"
    original = quarantine_execution._atomic_rename_no_replace
    crashed = False

    def crash_after_first_move(src: Path, dst: Path) -> None:
        nonlocal crashed
        original(src, dst)
        if not crashed:
            crashed = True
            raise KeyboardInterrupt

    monkeypatch.setattr(
        quarantine_execution, "_atomic_rename_no_replace", crash_after_first_move
    )
    with pytest.raises(QuarantineExecutionError, match="interrupted safely"):
        execute_quarantine(
            prepared,
            source,
            organized,
            quarantine,
            journal_path=journal,
        )

    monkeypatch.setattr(quarantine_execution, "_atomic_rename_no_replace", original)
    resumed = execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=journal,
        resume=True,
    )
    assert resumed.groups_completed == 3
    assert resumed.members_recovered == 1


def test_restore_crash_after_atomic_move_is_adopted_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, organized, quarantine, prepared = _fixture(tmp_path)
    _organize_winners(source, organized, prepared)
    quarantine_journal = tmp_path / "quarantine.jsonl"
    execute_quarantine(
        prepared,
        source,
        organized,
        quarantine,
        journal_path=quarantine_journal,
    )
    restore = prepare_quarantine_restore(prepared, quarantine_journal)
    restore_journal = tmp_path / "restore.jsonl"
    original = quarantine_execution._atomic_rename_no_replace
    crashed = False

    def crash_after_first_restore(src: Path, dst: Path) -> None:
        nonlocal crashed
        original(src, dst)
        if not crashed:
            crashed = True
            raise KeyboardInterrupt

    monkeypatch.setattr(
        quarantine_execution, "_atomic_rename_no_replace", crash_after_first_restore
    )
    with pytest.raises(QuarantineExecutionError, match="interrupted safely"):
        execute_quarantine_restore(
            restore,
            source,
            organized,
            quarantine,
            restore_journal_path=restore_journal,
        )

    monkeypatch.setattr(quarantine_execution, "_atomic_rename_no_replace", original)
    resumed = execute_quarantine_restore(
        restore,
        source,
        organized,
        quarantine,
        restore_journal_path=restore_journal,
        resume=True,
    )
    assert resumed.groups_completed == 3
    assert resumed.members_recovered == 1
