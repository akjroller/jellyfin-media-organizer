"""Unsupported no-overwrite rename (common on NAS mounts) must fail early and clearly."""

from __future__ import annotations

import errno
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
    UnsupportedRenameError,
    execute_apply,
    probe_no_replace_rename,
)
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.rollback_execution import (
    RollbackExecutionError,
    execute_rollback,
    prepare_rollback,
)

pytestmark = pytest.mark.local


def _unsupported(_source: Path, _destination: Path) -> None:
    apply_execution._raise_rename_errno(errno.EINVAL, _destination)


def _fingerprint(path: Path) -> SourceFingerprint:
    stat = path.stat()
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _setup(tmp_path: Path) -> tuple[Path, Path, PreparedApply, ApplyMember]:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    (source_root / "Release").mkdir(parents=True)
    destination_root.mkdir()
    video = source_root / "Release" / "episode.mkv"
    video.write_bytes(b"synthetic-video")
    member = ApplyMember(
        role=ApplyMemberRole.VIDEO,
        source_relative_path="Release/episode.mkv",
        destination_relative_path="Example (2026)/Season 01/episode.mkv",
        fingerprint=_fingerprint(video),
    )
    prepared = PreparedApply(
        contract=ApplyContract(
            plan_sha256="a" * 64,
            groups=(ApplyOperationGroup(group_id="op-example", members=(member,)),),
        ),
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
    )
    return source_root, destination_root, prepared, member


def test_probe_passes_and_leaves_the_directory_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    (tmp_path / "existing.mkv").write_bytes(b"media")

    probe_no_replace_rename(tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["existing.mkv"]
    assert (tmp_path / "existing.mkv").read_bytes() == b"media"


@pytest.mark.parametrize(
    "code",
    sorted(apply_execution._UNSUPPORTED_RENAME_ERRNOS),
)
def test_unsupported_rename_errnos_get_a_clear_message(code: int) -> None:
    with pytest.raises(UnsupportedRenameError, match="NFS, SMB/CIFS, and FUSE"):
        apply_execution._raise_rename_errno(code, Path("target"))


def test_other_rename_errnos_stay_ordinary_os_errors() -> None:
    with pytest.raises(FileExistsError):
        apply_execution._raise_rename_errno(errno.EEXIST, Path("target"))
    with pytest.raises(PermissionError):
        apply_execution._raise_rename_errno(errno.EACCES, Path("target"))


def test_probe_reports_unsupported_filesystem_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apply_execution, "_atomic_rename_no_replace", _unsupported)

    with pytest.raises(UnsupportedRenameError, match="does not support atomic"):
        probe_no_replace_rename(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_probe_fails_closed_when_rename_would_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apply_execution, "_atomic_rename_no_replace", os.replace)

    with pytest.raises(UnsupportedRenameError, match="overwrote an existing file"):
        probe_no_replace_rename(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_probe_reports_an_unwritable_destination(tmp_path: Path) -> None:
    with pytest.raises(ApplyExecutionError, match="check that it is writable"):
        probe_no_replace_rename(tmp_path / "missing")


def test_check_only_apply_fails_before_any_change_on_unsupported_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, destination_root, prepared, _member = _setup(tmp_path)
    monkeypatch.setattr(apply_execution, "_atomic_rename_no_replace", _unsupported)

    with pytest.raises(UnsupportedRenameError, match="does not support atomic"):
        execute_apply(
            prepared, source_root, destination_root, journal_path=None, check_only=True
        )

    assert (source_root / "Release" / "episode.mkv").is_file()
    assert list(destination_root.iterdir()) == []


def test_real_apply_fails_before_journal_or_move_on_unsupported_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, destination_root, prepared, _member = _setup(tmp_path)
    journal = tmp_path / "apply.jsonl"
    monkeypatch.setattr(apply_execution, "_atomic_rename_no_replace", _unsupported)

    with pytest.raises(UnsupportedRenameError):
        execute_apply(prepared, source_root, destination_root, journal_path=journal)

    assert not journal.exists()
    assert (source_root / "Release" / "episode.mkv").is_file()
    assert list(destination_root.iterdir()) == []


def test_mid_run_unsupported_rename_keeps_the_clear_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, destination_root, prepared, _member = _setup(tmp_path)
    monkeypatch.setattr(apply_execution, "probe_no_replace_rename", lambda _root: None)
    monkeypatch.setattr(apply_execution, "_atomic_rename_no_replace", _unsupported)

    with pytest.raises(
        ApplyExecutionError, match="rolled back safely: this filesystem does not"
    ):
        execute_apply(
            prepared,
            source_root,
            destination_root,
            journal_path=tmp_path / "apply.jsonl",
        )

    assert (source_root / "Release" / "episode.mkv").is_file()


def test_rollback_check_only_probes_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, destination_root, prepared, _member = _setup(tmp_path)
    apply_journal = tmp_path / "apply.jsonl"
    execute_apply(prepared, source_root, destination_root, journal_path=apply_journal)
    rollback = prepare_rollback(prepared, apply_journal)
    probed: list[Path] = []

    def failing_probe(directory: Path) -> None:
        probed.append(directory)
        raise UnsupportedRenameError("synthetic unsupported filesystem")

    monkeypatch.setattr(
        "jellyfin_show_organizer.rollback_execution.probe_no_replace_rename",
        failing_probe,
    )

    with pytest.raises((UnsupportedRenameError, RollbackExecutionError)):
        execute_rollback(
            rollback,
            source_root,
            destination_root,
            rollback_journal_path=None,
            check_only=True,
        )
    assert probed == [destination_root.resolve()]
