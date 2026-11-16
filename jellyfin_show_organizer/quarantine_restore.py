"""Restore duplicate losers that a completed quarantine moved out of the library.

Restore has its own approval token and journal, and only reverses a fully
completed quarantine run, member by member, with the same no-overwrite rename.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .apply_execution import (
    ApplyExecutionError,
    _atomic_rename_no_replace,
    _journal_lock,
    _release_journal_lock,
    probe_no_replace_rename,
)
from .quarantine_contract import (
    QuarantineMember,
)
from .quarantine_execution import (
    PreparedQuarantine,
    QuarantineExecutionError,
    _candidate_path,
    _journal_state,
    _JournalState,
    _mapping,
    _member_key,
    _member_state,
    _QuarantineJournal,
    _sha256_file,
    _timestamp,
    _validate_journal_location,
    _validate_roots,
)

QUARANTINE_RESTORE_JOURNAL_SCHEMA_VERSION = 1


_RESTORE_EVENTS = frozenset(
    {
        "restore-started",
        "group-started",
        "member-started",
        "member-completed",
        "group-failed",
        "group-completed",
        "restore-completed",
    }
)


@dataclass(frozen=True, slots=True)
class PreparedQuarantineRestore:
    prepared: PreparedQuarantine
    quarantine_journal_path: Path
    quarantine_journal_sha256: str


@dataclass(frozen=True, slots=True)
class QuarantineRestoreResult:
    quarantine_plan_sha256: str
    quarantine_journal_sha256: str
    groups_total: int
    members_total: int
    groups_completed: int
    members_restored: int
    members_recovered: int
    journal_path: Path | None
    check_only: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "quarantine_plan_sha256": self.quarantine_plan_sha256,
            "quarantine_journal_sha256": self.quarantine_journal_sha256,
            "groups_total": self.groups_total,
            "members_total": self.members_total,
            "groups_completed": self.groups_completed,
            "members_restored": self.members_restored,
            "members_recovered": self.members_recovered,
            "journal_path": str(self.journal_path) if self.journal_path else None,
            "check_only": self.check_only,
        }


def _restore_state(
    member: QuarantineMember,
    source_root: Path,
    quarantine_root: Path,
) -> str:
    state = _member_state(member, source_root, quarantine_root)
    return "pending" if state == "quarantined" else "restored"


def quarantine_restore_token(
    prepared: PreparedQuarantineRestore,
    source_root: Path,
    organized_root: Path,
    quarantine_root: Path,
) -> str:
    source, organized, quarantine = _validate_roots(
        source_root, organized_root, quarantine_root
    )
    roots = hashlib.sha256(
        (str(source) + "\0" + str(organized) + "\0" + str(quarantine)).encode("utf-8")
    ).hexdigest()
    return ":".join(
        (
            "QUARANTINE-RESTORE",
            prepared.prepared.plan.sha256,
            prepared.quarantine_journal_sha256,
            prepared.prepared.plan.source_revision,
            roots,
        )
    )


class _RestoreJournal:
    def __init__(
        self,
        path: Path,
        prepared: PreparedQuarantineRestore,
        *,
        resume: bool,
    ) -> None:
        self.path = path
        self.prepared = prepared
        self.entries: list[dict[str, object]] = []
        if resume:
            if not path.is_file():
                raise QuarantineExecutionError("resume restore journal does not exist")
            self.entries = self._read()
        elif os.path.lexists(path):
            raise QuarantineExecutionError(
                "restore journal already exists; use --resume"
            )

    def _read(self) -> list[dict[str, object]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise QuarantineExecutionError("restore journal is unreadable") from exc
        entries: list[dict[str, object]] = []
        for sequence, line in enumerate(lines, 1):
            try:
                entry = dict(_mapping(json.loads(line), "restore journal entry"))
            except json.JSONDecodeError as exc:
                raise QuarantineExecutionError(
                    "restore journal contains invalid JSON"
                ) from exc
            if entry.get("schema_version") != QUARANTINE_RESTORE_JOURNAL_SCHEMA_VERSION:
                raise QuarantineExecutionError("unsupported restore journal schema")
            if entry.get("sequence") != sequence:
                raise QuarantineExecutionError(
                    "restore journal sequence is not contiguous"
                )
            if entry.get("event") not in _RESTORE_EVENTS:
                raise QuarantineExecutionError(
                    "restore journal contains an unknown event"
                )
            if (
                entry.get("quarantine_plan_sha256")
                != self.prepared.prepared.plan.sha256
            ):
                raise QuarantineExecutionError(
                    "restore journal belongs to another quarantine plan"
                )
            if (
                entry.get("quarantine_journal_sha256")
                != self.prepared.quarantine_journal_sha256
            ):
                raise QuarantineExecutionError(
                    "restore journal belongs to another quarantine journal"
                )
            entries.append(entry)
        if not entries:
            raise QuarantineExecutionError("resume restore journal is empty")
        return entries

    def append(
        self,
        event: str,
        *,
        group_id: str | None = None,
        member: QuarantineMember | None = None,
        result: str,
        detail: str | None = None,
        recovery: str | None = None,
    ) -> None:
        entry: dict[str, object] = {
            "schema_version": QUARANTINE_RESTORE_JOURNAL_SCHEMA_VERSION,
            "sequence": len(self.entries) + 1,
            "timestamp_utc": _timestamp(),
            "quarantine_plan_sha256": self.prepared.prepared.plan.sha256,
            "quarantine_journal_sha256": self.prepared.quarantine_journal_sha256,
            "event": event,
            "group_id": group_id,
            "role": member.role.value if member else None,
            "source_relative_path": member.source_relative_path if member else None,
            "quarantine_relative_path": member.source_relative_path if member else None,
            "result": result,
            "detail": detail,
            "recovery": recovery,
        }
        payload = (
            json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        mode = "ab" if self.entries else "xb"
        try:
            with self.path.open(mode) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise QuarantineExecutionError(
                "could not durably append the restore journal"
            ) from exc
        self.entries.append(entry)

    def state(self) -> _JournalState:
        return _journal_state(self.entries, completion_event="restore-completed")


def _validate_completed_quarantine_journal(
    prepared: PreparedQuarantine,
    path: Path,
) -> str:
    if path.is_symlink() or not path.is_file():
        raise QuarantineExecutionError(
            "quarantine restore requires a regular completed quarantine journal"
        )
    journal = _QuarantineJournal(path, prepared, resume=True)
    state = journal.state()
    expected_groups = {group.group_id for group in prepared.plan.groups}
    expected_members = {
        _member_key(group.group_id, member)
        for group in prepared.plan.groups
        for member in group.members
    }
    if not state.run_completed or set(state.completed_groups) != expected_groups:
        raise QuarantineExecutionError(
            "quarantine journal does not describe one completed quarantine run"
        )
    if set(state.completed_members) != expected_members:
        raise QuarantineExecutionError(
            "quarantine journal does not contain every duplicate loser member"
        )
    return _sha256_file(path, "quarantine journal")


def prepare_quarantine_restore(
    prepared: PreparedQuarantine,
    quarantine_journal_path: Path,
) -> PreparedQuarantineRestore:
    try:
        resolved = quarantine_journal_path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise QuarantineExecutionError("quarantine journal does not exist") from exc
    digest = _validate_completed_quarantine_journal(prepared, resolved)
    return PreparedQuarantineRestore(
        prepared=prepared,
        quarantine_journal_path=resolved,
        quarantine_journal_sha256=digest,
    )


def _restore_all_state(
    prepared: PreparedQuarantineRestore,
    source_root: Path,
    quarantine_root: Path,
    expected: str,
) -> None:
    for group in prepared.prepared.plan.groups:
        for member in group.members:
            if _restore_state(member, source_root, quarantine_root) != expected:
                raise QuarantineExecutionError(
                    f"quarantine restore expected every member to be {expected}"
                )


def execute_quarantine_restore(
    prepared: PreparedQuarantineRestore,
    source_root: Path,
    organized_root: Path,
    quarantine_root: Path,
    *,
    restore_journal_path: Path | None,
    check_only: bool = False,
    resume: bool = False,
) -> QuarantineRestoreResult:
    """Check or restore one fully completed duplicate quarantine run."""

    roots = _validate_roots(source_root, organized_root, quarantine_root)
    source_root, organized_root, quarantine_root = roots
    del organized_root
    members_total = sum(len(group.members) for group in prepared.prepared.plan.groups)
    current_sha = _sha256_file(prepared.quarantine_journal_path, "quarantine journal")
    if current_sha != prepared.quarantine_journal_sha256:
        raise QuarantineExecutionError(
            "quarantine journal changed after restore approval was prepared"
        )
    if check_only:
        if resume:
            raise QuarantineExecutionError(
                "--check-only cannot be combined with --resume"
            )
        _restore_all_state(prepared, source_root, quarantine_root, "pending")
        probe_no_replace_rename(quarantine_root)
        return QuarantineRestoreResult(
            quarantine_plan_sha256=prepared.prepared.plan.sha256,
            quarantine_journal_sha256=prepared.quarantine_journal_sha256,
            groups_total=len(prepared.prepared.plan.groups),
            members_total=members_total,
            groups_completed=0,
            members_restored=0,
            members_recovered=0,
            journal_path=None,
            check_only=True,
        )
    if restore_journal_path is None:
        raise QuarantineExecutionError("restore requires an explicit restore journal")
    restore_journal_path = _validate_journal_location(
        restore_journal_path, roots, label="restore journal"
    )
    quarantine_lock = _journal_lock(prepared.quarantine_journal_path)
    restore_lock = _journal_lock(restore_journal_path)
    restored_count = 0
    recovered_count = 0
    try:
        probe_no_replace_rename(quarantine_root)
        _validate_completed_quarantine_journal(
            prepared.prepared, prepared.quarantine_journal_path
        )
        journal = _RestoreJournal(restore_journal_path, prepared, resume=resume)
        if not resume:
            _restore_all_state(prepared, source_root, quarantine_root, "pending")
            journal.append("restore-started", result="started")
        state = journal.state()
        if state.run_completed:
            _restore_all_state(prepared, source_root, quarantine_root, "restored")
            return QuarantineRestoreResult(
                quarantine_plan_sha256=prepared.prepared.plan.sha256,
                quarantine_journal_sha256=prepared.quarantine_journal_sha256,
                groups_total=len(prepared.prepared.plan.groups),
                members_total=members_total,
                groups_completed=len(prepared.prepared.plan.groups),
                members_restored=0,
                members_recovered=0,
                journal_path=restore_journal_path,
                check_only=False,
            )
        completed_groups = set(state.completed_groups)
        for group in reversed(prepared.prepared.plan.groups):
            if group.group_id in completed_groups:
                for member in group.members:
                    if (
                        _restore_state(member, source_root, quarantine_root)
                        != "restored"
                    ):
                        raise QuarantineExecutionError(
                            "completed restore group no longer matches filesystem"
                        )
                continue
            current = journal.state()
            pending: list[QuarantineMember] = []
            recovered: list[QuarantineMember] = []
            for member in reversed(group.members):
                key = _member_key(group.group_id, member)
                member_state = _restore_state(member, source_root, quarantine_root)
                if key in current.completed_members:
                    if member_state != "restored":
                        raise QuarantineExecutionError(
                            "restore journal and filesystem disagree"
                        )
                    recovered.append(member)
                    continue
                if key in current.started_members and member_state == "restored":
                    journal.append(
                        "member-completed",
                        group_id=group.group_id,
                        member=member,
                        result="recovered-after-interruption",
                    )
                    recovered.append(member)
                    recovered_count += 1
                    continue
                if member_state != "pending":
                    raise QuarantineExecutionError(
                        "restore member changed without matching journal evidence"
                    )
                pending.append(member)
            journal.append(
                "group-started",
                group_id=group.group_id,
                result="resumed" if recovered else "started",
            )
            try:
                for member in pending:
                    source = _candidate_path(
                        source_root, member.source_relative_path, require_parent=True
                    )
                    quarantined = _candidate_path(
                        quarantine_root,
                        member.source_relative_path,
                        require_parent=True,
                    )
                    if (
                        _restore_state(member, source_root, quarantine_root)
                        != "pending"
                    ):
                        raise QuarantineExecutionError(
                            "restore member changed immediately before restore"
                        )
                    journal.append(
                        "member-started",
                        group_id=group.group_id,
                        member=member,
                        result="started",
                    )
                    _atomic_rename_no_replace(quarantined, source)
                    if (
                        _restore_state(member, source_root, quarantine_root)
                        != "restored"
                    ):
                        raise QuarantineExecutionError(
                            "restored duplicate loser fingerprint is invalid"
                        )
                    journal.append(
                        "member-completed",
                        group_id=group.group_id,
                        member=member,
                        result="completed",
                    )
                    restored_count += 1
                journal.append(
                    "group-completed",
                    group_id=group.group_id,
                    result="completed",
                )
                completed_groups.add(group.group_id)
            except (Exception, KeyboardInterrupt) as exc:
                recovery = (
                    "leave already restored files at their original source paths; do "
                    "not overwrite source or quarantine files; inspect the group and "
                    "use --resume only after the state is unambiguous"
                )
                try:
                    journal.append(
                        "group-failed",
                        group_id=group.group_id,
                        result="incomplete",
                        detail=f"{type(exc).__name__}: {exc}",
                        recovery=recovery,
                    )
                except QuarantineExecutionError:
                    pass
                if isinstance(exc, QuarantineExecutionError):
                    raise
                raise QuarantineExecutionError(
                    f"quarantine restore interrupted safely: {exc}; {recovery}"
                ) from exc
        _restore_all_state(prepared, source_root, quarantine_root, "restored")
        journal.append("restore-completed", result="completed")
        return QuarantineRestoreResult(
            quarantine_plan_sha256=prepared.prepared.plan.sha256,
            quarantine_journal_sha256=prepared.quarantine_journal_sha256,
            groups_total=len(prepared.prepared.plan.groups),
            members_total=members_total,
            groups_completed=len(completed_groups),
            members_restored=restored_count,
            members_recovered=recovered_count,
            journal_path=restore_journal_path,
            check_only=False,
        )
    except ApplyExecutionError as exc:
        raise QuarantineExecutionError(str(exc)) from exc
    finally:
        _release_journal_lock(restore_lock)
        _release_journal_lock(quarantine_lock)
