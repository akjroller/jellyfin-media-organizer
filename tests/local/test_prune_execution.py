"""Empty-source-folder cleanup removes only what an apply emptied, and undoes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jellyfin_show_organizer.apply_contract import (
    ApplyContract,
    ApplyMember,
    ApplyMemberRole,
    ApplyOperationGroup,
)
from jellyfin_show_organizer.apply_execution import PreparedApply, execute_apply
from jellyfin_show_organizer.entrypoint import main
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.prune_cli import PRUNE_FAILED_EXIT
from jellyfin_show_organizer.prune_execution import (
    PruneError,
    execute_prune,
    plan_prune,
    restore_pruned_dirs,
)
from jellyfin_show_organizer.rollback_execution import (
    RollbackExecutionError,
    execute_rollback,
    prepare_rollback,
)

pytestmark = pytest.mark.local


def _fingerprint(path: Path) -> SourceFingerprint:
    stat = path.stat()
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _write(path: Path, payload: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _applied(
    tmp_path: Path, moves: dict[str, str], extra_files: tuple[str, ...] = ()
) -> tuple[Path, Path, PreparedApply, Path]:
    """Apply one group per video in ``moves`` and return the completed journal."""

    source = tmp_path / "Shows"
    destination = tmp_path / "Organized"
    destination.mkdir(parents=True)
    groups = []
    for index, (relative, target) in enumerate(sorted(moves.items())):
        video = _write(source / relative, f"video-{index}".encode())
        member = ApplyMember(
            role=ApplyMemberRole.VIDEO,
            source_relative_path=relative,
            destination_relative_path=target,
            fingerprint=_fingerprint(video),
        )
        groups.append(ApplyOperationGroup(group_id=f"op-{index}", members=(member,)))
    for relative in extra_files:
        _write(source / relative)
    prepared = PreparedApply(
        contract=ApplyContract(plan_sha256="a" * 64, groups=tuple(groups)),
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
    )
    journal = tmp_path / "apply.jsonl"
    execute_apply(prepared, source, destination, journal_path=journal)
    return source, destination, prepared, journal


def test_check_lists_only_folders_the_apply_emptied(tmp_path: Path) -> None:
    source, _destination, _prepared, journal = _applied(
        tmp_path,
        {
            "Show A/Season 1/Disc 1/episode.mkv": "A/Season 01/A S01E01.mkv",
            "Show B/Season 1/episode.mkv": "B/Season 01/B S01E01.mkv",
        },
        extra_files=(
            "Show B/notes.txt",
            "Untouched/Empty Neighbour/.keep",
        ),
    )
    (source / "Untouched" / "Empty Neighbour" / ".keep").unlink()

    plan = plan_prune(source, journal)

    assert plan.directories == (
        "Show A/Season 1/Disc 1",
        "Show A/Season 1",
        "Show B/Season 1",
        "Show A",
    )
    assert (source / "Show A" / "Season 1" / "Disc 1").is_dir()
    assert plan.token.startswith("PRUNE:")


def test_a_leftover_file_keeps_its_folder_and_parents(tmp_path: Path) -> None:
    source, _destination, _prepared, journal = _applied(
        tmp_path,
        {"Show/Season 1/episode.mkv": "Show/Season 01/S01E01.mkv"},
        extra_files=("Show/Season 1/Thumbs.db",),
    )

    assert plan_prune(source, journal).directories == ()


def test_prune_removes_confirmed_folders_and_journals_them(tmp_path: Path) -> None:
    source, _destination, _prepared, journal = _applied(
        tmp_path, {"Show/Season 1/episode.mkv": "Show/Season 01/S01E01.mkv"}
    )
    plan = plan_prune(source, journal)
    prune_journal = tmp_path / "prune.jsonl"

    with pytest.raises(PruneError, match="confirmation does not match"):
        execute_prune(plan, journal, confirm="PRUNE:wrong", journal_path=prune_journal)
    with pytest.raises(PruneError, match="outside the source root"):
        execute_prune(
            plan, journal, confirm=plan.token, journal_path=source / "prune.jsonl"
        )
    with pytest.raises(PruneError, match="distinct from the apply journal"):
        execute_prune(plan, journal, confirm=plan.token, journal_path=journal)
    assert (source / "Show" / "Season 1").is_dir()

    result = execute_prune(
        plan, journal, confirm=plan.token, journal_path=prune_journal
    )

    assert result.removed == ("Show/Season 1", "Show")
    assert source.is_dir() and list(source.iterdir()) == []
    events = [
        json.loads(line)["event"] for line in prune_journal.read_text().splitlines()
    ]
    assert events == [
        "prune-started",
        "directory-removed",
        "directory-removed",
        "prune-completed",
    ]


def test_prune_refuses_when_a_folder_changed_after_the_check(tmp_path: Path) -> None:
    source, _destination, _prepared, journal = _applied(
        tmp_path, {"Show/Season 1/episode.mkv": "Show/Season 01/S01E01.mkv"}
    )
    plan = plan_prune(source, journal)
    _write(source / "Show" / "Season 1" / "new.nfo")

    with pytest.raises(PruneError, match="changed since the check"):
        execute_prune(
            plan, journal, confirm=plan.token, journal_path=tmp_path / "prune.jsonl"
        )
    assert (source / "Show" / "Season 1" / "new.nfo").is_file()


def test_unfinished_apply_journal_is_refused(tmp_path: Path) -> None:
    source, _destination, _prepared, journal = _applied(
        tmp_path, {"Show/Season 1/episode.mkv": "Show/Season 01/S01E01.mkv"}
    )
    lines = journal.read_text(encoding="utf-8").splitlines()
    journal.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(PruneError, match="completed apply journal"):
        plan_prune(source, journal)


def test_restore_lets_rollback_succeed_after_a_prune(tmp_path: Path) -> None:
    source, destination, prepared, journal = _applied(
        tmp_path, {"Show/Season 1/episode.mkv": "Show/Season 01/S01E01.mkv"}
    )
    rollback = prepare_rollback(prepared, journal)
    plan = plan_prune(source, journal)
    prune_journal = tmp_path / "prune.jsonl"
    execute_prune(plan, journal, confirm=plan.token, journal_path=prune_journal)

    with pytest.raises(RollbackExecutionError, match="parent is missing"):
        execute_rollback(
            rollback, source, destination, rollback_journal_path=None, check_only=True
        )

    assert restore_pruned_dirs(source, prune_journal) == ("Show", "Show/Season 1")
    assert restore_pruned_dirs(source, prune_journal) == ()

    execute_rollback(
        rollback,
        source,
        destination,
        rollback_journal_path=tmp_path / "rollback.jsonl",
    )
    assert (source / "Show" / "Season 1" / "episode.mkv").read_bytes() == b"video-0"


def test_cli_check_then_prune_then_restore(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, _destination, _prepared, journal = _applied(
        tmp_path, {"Show/Season 1/episode.mkv": "Show/Season 01/S01E01.mkv"}
    )
    common = ["--source-root", str(source), "--apply-journal", str(journal)]

    assert main(["prune-empty-dirs", *common, "--check-only", "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["directories"] == ["Show/Season 1", "Show"]
    token = checked["confirmation_token"]

    prune_journal = tmp_path / "prune.jsonl"
    assert (
        main(
            [
                "prune-empty-dirs",
                *common,
                "--journal",
                str(prune_journal),
                "--confirm-prune",
                "PRUNE:wrong",
            ]
        )
        == PRUNE_FAILED_EXIT
    )
    assert "Prune failed safely" in capsys.readouterr().err
    assert (
        main(
            [
                "prune-empty-dirs",
                *common,
                "--journal",
                str(prune_journal),
                "--confirm-prune",
                token,
            ]
        )
        == 0
    )
    assert "Removed 2 empty source folder(s)" in capsys.readouterr().out
    assert not (source / "Show").exists()

    assert (
        main(
            [
                "restore-pruned-dirs",
                "--source-root",
                str(source),
                "--prune-journal",
                str(prune_journal),
            ]
        )
        == 0
    )
    assert "Recreated 2 folder(s)." in capsys.readouterr().out
    assert (source / "Show" / "Season 1").is_dir()
