from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .apply_contract import ApplyMember
from .apply_execution import (
    ApplyExecutionError,
    PreparedApply,
    _Journal,
    _atomic_rename_no_replace,
    _fingerprint_matches,
    _journal_lock,
    _member_key,
    _release_journal_lock,
    _safe_existing_path,
    _verify_source,
)
from .apply_validation import validate_apply_roots

_ROLLBACK_JOURNAL_SCHEMA_VERSION = 1
_ROLLBACK_EVENTS = frozenset(
    {
        "rollback-started",
        "group-started",
        "member-started",
        "member-completed",
        "group-failed",
        "group-completed",
        "rollback-completed",
    }
)


class RollbackExecutionError(RuntimeError):
    """Raised when a completed apply cannot be reversed safely."""


@dataclass(frozen=True, slots=True)
class PreparedRollback:
    prepared_apply: PreparedApply
    apply_journal_path: Path
    apply_journal_sha256: str


@dataclass(frozen=True, slots=True)
class RollbackExecutionResult:
    plan_sha256: str
    review_session_sha256: str
    apply_journal_sha256: str
    groups_total: int
    groups_completed: int
    members_restored: int
    members_recovered: int
    rollback_journal_path: Path | None
    check_only: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "plan_sha256": self.plan_sha256,
            "review_session_sha256": self.review_session_sha256,
            "apply_journal_sha256": self.apply_journal_sha256,
            "groups_total": self.groups_total,
            "groups_completed": self.groups_completed,
            "members_restored": self.members_restored,
            "members_recovered": self.members_recovered,
            "rollback_journal_path": (
                str(self.rollback_journal_path)
                if self.rollback_journal_path is not None
                else None
            ),
            "check_only": self.check_only,
        }


@dataclass(frozen=True, slots=True)
class _RollbackJournalState:
    completed_groups: frozenset[str]
    completed_members: frozenset[tuple[str, str, str]]
    started_members: frozenset[tuple[str, str, str]]
    run_completed: bool


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RollbackExecutionError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise RollbackExecutionError("apply journal is unreadable") from exc
    return digest.hexdigest()


def _validate_completed_apply_journal(
    prepared: PreparedApply, apply_journal_path: Path
) -> None:
    try:
        journal = _Journal(apply_journal_path, prepared, resume=True)
        state = journal.state()
    except ApplyExecutionError as exc:
        raise RollbackExecutionError(str(exc)) from exc
    if not state.run_completed:
        raise RollbackExecutionError(
            "rollback requires an apply journal with a completed run"
        )
    expected_groups = {group.group_id for group in prepared.contract.groups}
    if set(state.completed_groups) != expected_groups:
        raise RollbackExecutionError(
            "completed apply journal does not contain every approved operation group"
        )
    expected_members = {
        _member_key(group.group_id, member)
        for group in prepared.contract.groups
        for member in group.moving_members
    }
    if set(state.completed_members) != expected_members:
        raise RollbackExecutionError(
            "completed apply journal does not contain every approved member move"
        )


def prepare_rollback(
    prepared_apply: PreparedApply, apply_journal_path: Path
) -> PreparedRollback:
    """Bind rollback to one exact, completed, untampered apply journal."""

    if apply_journal_path.is_symlink():
        raise RollbackExecutionError("apply journal cannot be a symlink")
    try:
        resolved = apply_journal_path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise RollbackExecutionError("apply journal does not exist") from exc
    if not resolved.is_file():
        raise RollbackExecutionError("apply journal must be a regular file")
    _validate_completed_apply_journal(prepared_apply, resolved)
    return PreparedRollback(
        prepared_apply=prepared_apply,
        apply_journal_path=resolved,
        apply_journal_sha256=_sha256_file(resolved),
    )


def rollback_token(
    prepared: PreparedRollback, source_root: Path, destination_root: Path
) -> str:
    """Return exact rollback confirmation bound to apply evidence and roots."""

    source, destination = validate_apply_roots(source_root, destination_root)
    roots = hashlib.sha256(
        (str(source) + "\0" + str(destination)).encode("utf-8")
    ).hexdigest()
    apply = prepared.prepared_apply
    return ":".join(
        (
            "ROLLBACK",
            apply.contract.plan_sha256,
            apply.review_session_sha256,
            apply.source_revision,
            prepared.apply_journal_sha256,
            roots,
        )
    )


def _validate_journal_location(
    path: Path,
    source_root: Path,
    destination_root: Path,
    *,
    label: str,
) -> Path:
    if path.is_symlink() or path.parent.is_symlink():
        raise RollbackExecutionError(f"{label} cannot be a symlink")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise RollbackExecutionError(f"{label} parent does not exist") from exc
    if parent == source_root or parent.is_relative_to(source_root):
        raise RollbackExecutionError(f"{label} must be outside the source root")
    if parent == destination_root or parent.is_relative_to(destination_root):
        raise RollbackExecutionError(f"{label} must be outside the destination root")
    return path


def _member_state(
    member: ApplyMember, source_root: Path, destination_root: Path
) -> str:
    try:
        source = _safe_existing_path(source_root, member.source_relative_path)
        destination = _safe_existing_path(
            destination_root, member.destination_relative_path
        )
    except ApplyExecutionError as exc:
        raise RollbackExecutionError(str(exc)) from exc
    source_exists = os.path.lexists(source)
    destination_exists = os.path.lexists(destination)
    source_matches = source_exists and _fingerprint_matches(source, member.fingerprint)
    destination_matches = destination_exists and _fingerprint_matches(
        destination, member.fingerprint
    )
    if not source_exists and destination_matches:
        return "pending"
    if source_matches and not destination_exists:
        return "restored"
    if source_exists and destination_exists:
        reason = "both the original source and organized destination exist"
    elif not source_exists and not destination_exists:
        reason = "both the original source and organized destination are missing"
    elif source_exists:
        reason = "the recreated source does not match the approved fingerprint"
    else:
        reason = "the organized destination does not match the approved fingerprint"
    raise RollbackExecutionError(
        "rollback state is ambiguous for "
        f"'{member.destination_relative_path}' -> '{member.source_relative_path}': "
        f"{reason}; do not overwrite either path; restore the expected state or "
        "inspect it manually before using --resume"
    )


def _verify_all_pending(
    prepared: PreparedRollback, source_root: Path, destination_root: Path
) -> None:
    for group in prepared.prepared_apply.contract.groups:
        for member in group.moving_members:
            if _member_state(member, source_root, destination_root) != "pending":
                raise RollbackExecutionError(
                    "rollback check-only requires every approved member to still be "
                    "at its organized destination"
                )


def _verify_all_restored(
    prepared: PreparedRollback, source_root: Path, destination_root: Path
) -> None:
    for group in prepared.prepared_apply.contract.groups:
        for member in group.moving_members:
            if _member_state(member, source_root, destination_root) != "restored":
                raise RollbackExecutionError(
                    "completed rollback verification found a member that was not "
                    "restored to its original source"
                )


class _RollbackJournal:
    def __init__(
        self, path: Path, prepared: PreparedRollback, *, resume: bool
    ) -> None:
        self.path = path
        self.prepared = prepared
        self.entries: list[dict[str, object]] = []
        if resume:
            if not path.is_file():
                raise RollbackExecutionError("resume rollback journal does not exist")
            self.entries = self._read()
        elif os.path.lexists(path):
            raise RollbackExecutionError(
                "rollback journal already exists; use --resume"
            )

    def _read(self) -> list[dict[str, object]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise RollbackExecutionError("rollback journal is unreadable") from exc
        entries: list[dict[str, object]] = []
        apply = self.prepared.prepared_apply
        for sequence, line in enumerate(lines, 1):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RollbackExecutionError(
                    "rollback journal contains invalid JSON"
                ) from exc
            entry = dict(_mapping(raw, "rollback journal entry"))
            if entry.get("schema_version") != _ROLLBACK_JOURNAL_SCHEMA_VERSION:
                raise RollbackExecutionError("unsupported rollback journal schema")
            if entry.get("sequence") != sequence:
                raise RollbackExecutionError(
                    "rollback journal sequence is not contiguous"
                )
            if entry.get("event") not in _ROLLBACK_EVENTS:
                raise RollbackExecutionError(
                    "rollback journal contains an unknown event"
                )
            if entry.get("plan_sha256") != apply.contract.plan_sha256:
                raise RollbackExecutionError("rollback journal belongs to another plan")
            if entry.get("review_session_sha256") != apply.review_session_sha256:
                raise RollbackExecutionError(
                    "rollback journal belongs to another review"
                )
            if entry.get("source_revision") != apply.source_revision:
                raise RollbackExecutionError(
                    "rollback journal belongs to another revision"
                )
            if (
                entry.get("apply_journal_sha256")
                != self.prepared.apply_journal_sha256
            ):
                raise RollbackExecutionError(
                    "rollback journal belongs to another apply journal"
                )
            entries.append(entry)
        if not entries:
            raise RollbackExecutionError("resume rollback journal is empty")
        return entries

    def append(
        self,
        event: str,
        *,
        group_id: str | None = None,
        member: ApplyMember | None = None,
        result: str,
        detail: str | None = None,
        recovery: str | None = None,
    ) -> None:
        apply = self.prepared.prepared_apply
        entry: dict[str, object] = {
            "schema_version": _ROLLBACK_JOURNAL_SCHEMA_VERSION,
            "sequence": len(self.entries) + 1,
            "timestamp_utc": _timestamp(),
            "plan_sha256": apply.contract.plan_sha256,
            "review_session_sha256": apply.review_session_sha256,
            "source_revision": apply.source_revision,
            "apply_journal_sha256": self.prepared.apply_journal_sha256,
            "event": event,
            "group_id": group_id,
            "role": member.role.value if member is not None else None,
            "source_relative_path": (
                member.source_relative_path if member is not None else None
            ),
            "destination_relative_path": (
                member.destination_relative_path if member is not None else None
            ),
            "pre_state": (
                {
                    "source": "absent",
                    "destination": "present-and-fingerprint-matched",
                    "size": member.fingerprint.size,
                    "mtime_ns": member.fingerprint.mtime_ns,
                    "sha256": member.fingerprint.sha256,
                }
                if member is not None
                else None
            ),
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
            raise RollbackExecutionError(
                "could not durably append the rollback journal"
            ) from exc
        self.entries.append(entry)

    def state(self) -> _RollbackJournalState:
        completed_groups: set[str] = set()
        completed_members: set[tuple[str, str, str]] = set()
        started_members: set[tuple[str, str, str]] = set()
        run_completed = False
        for entry in self.entries:
            event = cast(str, entry["event"])
            group_id = entry.get("group_id")
            source = entry.get("source_relative_path")
            destination = entry.get("destination_relative_path")
            key = None
            if all(isinstance(value, str) for value in (group_id, source, destination)):
                key = (
                    cast(str, group_id),
                    cast(str, source).replace("\\", "/").casefold(),
                    cast(str, destination).replace("\\", "/").casefold(),
                )
            if event == "member-started" and key is not None:
                started_members.add(key)
            elif event == "member-completed" and key is not None:
                completed_members.add(key)
                started_members.discard(key)
            elif event == "group-completed" and isinstance(group_id, str):
                completed_groups.add(group_id)
            elif event == "rollback-completed":
                run_completed = True
        return _RollbackJournalState(
            completed_groups=frozenset(completed_groups),
            completed_members=frozenset(completed_members),
            started_members=frozenset(started_members),
            run_completed=run_completed,
        )


def execute_rollback(
    prepared: PreparedRollback,
    source_root: Path,
    destination_root: Path,
    *,
    rollback_journal_path: Path | None,
    check_only: bool = False,
    resume: bool = False,
) -> RollbackExecutionResult:
    """Check or reverse one completed apply run in conservative reverse order."""

    source_root, destination_root = validate_apply_roots(source_root, destination_root)
    if check_only and resume:
        raise RollbackExecutionError("--check-only cannot be combined with --resume")
    current_apply_sha = _sha256_file(prepared.apply_journal_path)
    if current_apply_sha != prepared.apply_journal_sha256:
        raise RollbackExecutionError(
            "apply journal changed after rollback approval was prepared"
        )
    apply_lock = _journal_lock(prepared.apply_journal_path)
    try:
        _validate_completed_apply_journal(
            prepared.prepared_apply, prepared.apply_journal_path
        )
        if check_only:
            _verify_all_pending(prepared, source_root, destination_root)
            return RollbackExecutionResult(
                plan_sha256=prepared.prepared_apply.contract.plan_sha256,
                review_session_sha256=prepared.prepared_apply.review_session_sha256,
                apply_journal_sha256=prepared.apply_journal_sha256,
                groups_total=len(prepared.prepared_apply.contract.groups),
                groups_completed=0,
                members_restored=0,
                members_recovered=0,
                rollback_journal_path=None,
                check_only=True,
            )
        if rollback_journal_path is None:
            raise RollbackExecutionError(
                "rollback requires an explicit rollback journal path"
            )
        rollback_journal_path = _validate_journal_location(
            rollback_journal_path,
            source_root,
            destination_root,
            label="rollback journal",
        )
        if rollback_journal_path == prepared.apply_journal_path:
            raise RollbackExecutionError(
                "rollback journal must be distinct from the apply journal"
            )
        rollback_lock = _journal_lock(rollback_journal_path)
        restored_count = 0
        recovered_count = 0
        try:
            journal = _RollbackJournal(
                rollback_journal_path, prepared, resume=resume
            )
            if not resume:
                _verify_all_pending(prepared, source_root, destination_root)
                journal.append("rollback-started", result="started")
            state = journal.state()
            if state.run_completed:
                _verify_all_restored(prepared, source_root, destination_root)
                return RollbackExecutionResult(
                    plan_sha256=prepared.prepared_apply.contract.plan_sha256,
                    review_session_sha256=(
                        prepared.prepared_apply.review_session_sha256
                    ),
                    apply_journal_sha256=prepared.apply_journal_sha256,
                    groups_total=len(prepared.prepared_apply.contract.groups),
                    groups_completed=len(prepared.prepared_apply.contract.groups),
                    members_restored=0,
                    members_recovered=0,
                    rollback_journal_path=rollback_journal_path,
                    check_only=False,
                )

            completed_groups = set(state.completed_groups)
            for group in reversed(prepared.prepared_apply.contract.groups):
                members = list(reversed(group.moving_members))
                if group.group_id in completed_groups:
                    for member in members:
                        if _member_state(member, source_root, destination_root) != "restored":
                            raise RollbackExecutionError(
                                "completed rollback group no longer matches restored state"
                            )
                    continue

                current_state = journal.state()
                pending: list[ApplyMember] = []
                restored: list[ApplyMember] = []
                for member in members:
                    key = _member_key(group.group_id, member)
                    state_name = _member_state(member, source_root, destination_root)
                    if key in current_state.completed_members:
                        if state_name != "restored":
                            raise RollbackExecutionError(
                                "rollback journal says a member was restored but the "
                                "filesystem no longer matches"
                            )
                        restored.append(member)
                        continue
                    if key in current_state.started_members:
                        if state_name == "restored":
                            journal.append(
                                "member-completed",
                                group_id=group.group_id,
                                member=member,
                                result="recovered-after-interruption",
                            )
                            restored.append(member)
                            recovered_count += 1
                            continue
                        if state_name == "pending":
                            pending.append(member)
                            continue
                    if state_name != "pending":
                        raise RollbackExecutionError(
                            "rollback member changed state without matching journal evidence"
                        )
                    pending.append(member)

                journal.append(
                    "group-started",
                    group_id=group.group_id,
                    result="resumed" if restored else "started",
                )
                try:
                    for member in pending:
                        if _member_state(member, source_root, destination_root) != "pending":
                            raise RollbackExecutionError(
                                "rollback member changed after group validation"
                            )
                    for member in pending:
                        journal.append(
                            "member-started",
                            group_id=group.group_id,
                            member=member,
                            result="started",
                        )
                        source = _safe_existing_path(
                            source_root, member.source_relative_path
                        )
                        destination = _safe_existing_path(
                            destination_root, member.destination_relative_path
                        )
                        if _member_state(member, source_root, destination_root) != "pending":
                            raise RollbackExecutionError(
                                "rollback member changed immediately before restore"
                            )
                        _atomic_rename_no_replace(destination, source)
                        try:
                            _verify_source(member, source_root, destination_root)
                        except ApplyExecutionError as exc:
                            raise RollbackExecutionError(str(exc)) from exc
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
                        "leave already restored members at their original source paths; "
                        "do not overwrite source or destination files; correct the "
                        "reported ambiguity and rerun with --resume"
                    )
                    try:
                        journal.append(
                            "group-failed",
                            group_id=group.group_id,
                            result="incomplete",
                            detail=f"{type(exc).__name__}: {exc}",
                            recovery=recovery,
                        )
                    except RollbackExecutionError:
                        pass
                    if isinstance(exc, RollbackExecutionError):
                        raise
                    raise RollbackExecutionError(
                        f"rollback interrupted safely: {exc}; {recovery}"
                    ) from exc

            _verify_all_restored(prepared, source_root, destination_root)
            journal.append("rollback-completed", result="completed")
            return RollbackExecutionResult(
                plan_sha256=prepared.prepared_apply.contract.plan_sha256,
                review_session_sha256=prepared.prepared_apply.review_session_sha256,
                apply_journal_sha256=prepared.apply_journal_sha256,
                groups_total=len(prepared.prepared_apply.contract.groups),
                groups_completed=len(completed_groups),
                members_restored=restored_count,
                members_recovered=recovered_count,
                rollback_journal_path=rollback_journal_path,
                check_only=False,
            )
        finally:
            _release_journal_lock(rollback_lock)
    except ApplyExecutionError as exc:
        raise RollbackExecutionError(str(exc)) from exc
    finally:
        _release_journal_lock(apply_lock)


def total_rollback_members(prepared: PreparedRollback) -> int:
    return sum(
        len(group.moving_members)
        for group in prepared.prepared_apply.contract.groups
    )
