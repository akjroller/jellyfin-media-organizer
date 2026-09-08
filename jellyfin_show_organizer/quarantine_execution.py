from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .apply_execution import (
    ApplyExecutionError,
    PreparedApply,
    _atomic_rename_no_replace,
    _fingerprint_matches,
    _is_linklike,
    _journal_lock,
    _parts,
    _release_journal_lock,
)
from .quarantine_contract import (
    QuarantineContractError,
    QuarantineGroup,
    QuarantineMember,
    QuarantinePlan,
    load_quarantine_plan,
    validate_quarantine_plan_binding,
)

QUARANTINE_JOURNAL_SCHEMA_VERSION = 1
QUARANTINE_RESTORE_JOURNAL_SCHEMA_VERSION = 1
_QUARANTINE_EVENTS = frozenset(
    {
        "run-started",
        "group-started",
        "directory-create-started",
        "directory-created",
        "member-started",
        "member-completed",
        "member-rollback-started",
        "member-rollback-completed",
        "group-failed",
        "group-completed",
        "run-completed",
    }
)
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


class QuarantineExecutionError(RuntimeError):
    """Raised when duplicate quarantine or restore cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class PreparedQuarantine:
    prepared_apply: PreparedApply
    plan: QuarantinePlan


@dataclass(frozen=True, slots=True)
class PreparedQuarantineRestore:
    prepared: PreparedQuarantine
    quarantine_journal_path: Path
    quarantine_journal_sha256: str


@dataclass(frozen=True, slots=True)
class QuarantineExecutionResult:
    quarantine_plan_sha256: str
    plan_sha256: str
    review_session_sha256: str
    groups_total: int
    members_total: int
    winners_preapply: int
    winners_organized: int
    groups_completed: int
    members_moved: int
    members_recovered: int
    journal_path: Path | None
    check_only: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "quarantine_plan_sha256": self.quarantine_plan_sha256,
            "plan_sha256": self.plan_sha256,
            "review_session_sha256": self.review_session_sha256,
            "groups_total": self.groups_total,
            "members_total": self.members_total,
            "winners_preapply": self.winners_preapply,
            "winners_organized": self.winners_organized,
            "groups_completed": self.groups_completed,
            "members_moved": self.members_moved,
            "members_recovered": self.members_recovered,
            "journal_path": str(self.journal_path) if self.journal_path else None,
            "check_only": self.check_only,
        }


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


@dataclass(frozen=True, slots=True)
class _JournalState:
    completed_groups: frozenset[str]
    completed_members: frozenset[tuple[str, str]]
    started_members: frozenset[tuple[str, str]]
    run_completed: bool


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise QuarantineExecutionError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sha256_file(path: Path, label: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise QuarantineExecutionError(f"{label} is unreadable") from exc
    return digest.hexdigest()


def _validated_root(path: Path, label: str) -> Path:
    if not os.path.lexists(path):
        raise QuarantineExecutionError(f"{label} root does not exist")
    if _is_linklike(path):
        raise QuarantineExecutionError(f"{label} root cannot be a symlink or junction")
    if not path.is_dir():
        raise QuarantineExecutionError(f"{label} root must be a directory")
    return path.resolve(strict=True)


def _validate_roots(
    source_root: Path, organized_root: Path, quarantine_root: Path
) -> tuple[Path, Path, Path]:
    source = _validated_root(source_root, "source")
    organized = _validated_root(organized_root, "organized")
    quarantine = _validated_root(quarantine_root, "quarantine")
    if quarantine == source or quarantine.is_relative_to(source):
        raise QuarantineExecutionError("quarantine root must be outside the source root")
    if quarantine == organized or quarantine.is_relative_to(organized):
        raise QuarantineExecutionError(
            "quarantine root must be outside the organized library root"
        )
    if source.stat().st_dev != quarantine.stat().st_dev:
        raise QuarantineExecutionError(
            "cross-filesystem duplicate quarantine remains disabled"
        )
    return source, organized, quarantine


def _candidate_path(root: Path, relative_path: str, *, require_parent: bool) -> Path:
    current = root
    parts = _parts(relative_path)
    for part in parts[:-1]:
        current = current / part
        if not os.path.lexists(current):
            if require_parent:
                raise QuarantineExecutionError("required member parent is missing")
            break
        if _is_linklike(current) or not current.is_dir():
            raise QuarantineExecutionError(
                "member parent chain contains a link or non-directory"
            )
    return root.joinpath(*parts)


def _winner_state(
    group: QuarantineGroup,
    source_root: Path,
    organized_root: Path,
) -> str:
    source = _candidate_path(
        source_root, group.winner.source_relative_path, require_parent=False
    )
    organized = _candidate_path(
        organized_root, group.winner.organized_relative_path, require_parent=False
    )
    same_path = source == organized
    source_exists = os.path.lexists(source)
    organized_exists = os.path.lexists(organized)
    source_matches = source_exists and _fingerprint_matches(
        source, group.winner.fingerprint
    )
    organized_matches = organized_exists and _fingerprint_matches(
        organized, group.winner.fingerprint
    )
    if same_path and source_matches:
        return "organized"
    if source_matches and not organized_exists:
        return "preapply"
    if not source_exists and organized_matches:
        return "organized"
    if source_exists and organized_exists:
        reason = "winner exists at both original and organized paths"
    elif source_exists:
        reason = "winner original source fingerprint is wrong"
    elif organized_exists:
        reason = "winner organized destination fingerprint is wrong"
    else:
        reason = "winner is missing from both original and organized paths"
    raise QuarantineExecutionError(
        f"duplicate winner state is ambiguous for '{group.group_id}': {reason}"
    )


def _member_state(
    member: QuarantineMember,
    source_root: Path,
    quarantine_root: Path,
) -> str:
    source = _candidate_path(source_root, member.source_relative_path, require_parent=False)
    quarantined = _candidate_path(
        quarantine_root, member.source_relative_path, require_parent=False
    )
    source_exists = os.path.lexists(source)
    quarantine_exists = os.path.lexists(quarantined)
    source_matches = source_exists and _fingerprint_matches(source, member.fingerprint)
    quarantine_matches = quarantine_exists and _fingerprint_matches(
        quarantined, member.fingerprint
    )
    if source_matches and not quarantine_exists:
        return "pending"
    if not source_exists and quarantine_matches:
        return "quarantined"
    if source_exists and quarantine_exists:
        reason = "both source and quarantine destination exist"
    elif not source_exists and not quarantine_exists:
        reason = "both source and quarantine destination are missing"
    elif source_exists:
        reason = "source fingerprint no longer matches the reviewed plan"
    else:
        reason = "quarantine destination fingerprint is invalid"
    raise QuarantineExecutionError(
        f"quarantine state is ambiguous for '{member.source_relative_path}': {reason}"
    )


def _restore_state(
    member: QuarantineMember,
    source_root: Path,
    quarantine_root: Path,
) -> str:
    state = _member_state(member, source_root, quarantine_root)
    return "pending" if state == "quarantined" else "restored"


def prepare_quarantine(
    prepared_apply: PreparedApply,
    manifest: object,
    quarantine_plan_payload: bytes,
    *,
    approved_quarantine_plan_sha256: str,
) -> PreparedQuarantine:
    try:
        supplied = load_quarantine_plan(quarantine_plan_payload)
        expected = validate_quarantine_plan_binding(supplied, manifest, prepared_apply)
    except QuarantineContractError as exc:
        raise QuarantineExecutionError(str(exc)) from exc
    if supplied.sha256 != approved_quarantine_plan_sha256:
        raise QuarantineExecutionError(
            "approved quarantine-plan SHA-256 does not match the supplied artifact"
        )
    if expected.sha256 != supplied.sha256:
        raise QuarantineExecutionError("quarantine plan canonical hash mismatch")
    return PreparedQuarantine(prepared_apply=prepared_apply, plan=expected)


def quarantine_token(
    prepared: PreparedQuarantine,
    source_root: Path,
    organized_root: Path,
    quarantine_root: Path,
) -> str:
    source, organized, quarantine = _validate_roots(
        source_root, organized_root, quarantine_root
    )
    roots = hashlib.sha256(
        (str(source) + "\0" + str(organized) + "\0" + str(quarantine)).encode(
            "utf-8"
        )
    ).hexdigest()
    return ":".join(
        (
            "QUARANTINE",
            prepared.plan.sha256,
            prepared.plan.plan_sha256,
            prepared.plan.review_session_sha256,
            prepared.plan.source_revision,
            roots,
        )
    )


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
        (str(source) + "\0" + str(organized) + "\0" + str(quarantine)).encode(
            "utf-8"
        )
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


def _member_key(group_id: str, member: QuarantineMember) -> tuple[str, str]:
    return (group_id, member.source_relative_path.replace("\\", "/").casefold())


def _validate_journal_location(
    path: Path,
    roots: tuple[Path, Path, Path],
    *,
    label: str,
) -> Path:
    if path.is_symlink() or path.parent.is_symlink():
        raise QuarantineExecutionError(f"{label} cannot be a symlink")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise QuarantineExecutionError(f"{label} parent does not exist") from exc
    if any(parent == root or parent.is_relative_to(root) for root in roots):
        raise QuarantineExecutionError(f"{label} must be outside all media roots")
    return path


class _QuarantineJournal:
    def __init__(
        self,
        path: Path,
        prepared: PreparedQuarantine,
        *,
        resume: bool,
    ) -> None:
        self.path = path
        self.prepared = prepared
        self.entries: list[dict[str, object]] = []
        if resume:
            if not path.is_file():
                raise QuarantineExecutionError("resume quarantine journal does not exist")
            self.entries = self._read()
        elif os.path.lexists(path):
            raise QuarantineExecutionError(
                "quarantine journal already exists; use --resume"
            )

    def _read(self) -> list[dict[str, object]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise QuarantineExecutionError("quarantine journal is unreadable") from exc
        entries: list[dict[str, object]] = []
        for sequence, line in enumerate(lines, 1):
            try:
                entry = dict(_mapping(json.loads(line), "quarantine journal entry"))
            except json.JSONDecodeError as exc:
                raise QuarantineExecutionError(
                    "quarantine journal contains invalid JSON"
                ) from exc
            if entry.get("schema_version") != QUARANTINE_JOURNAL_SCHEMA_VERSION:
                raise QuarantineExecutionError("unsupported quarantine journal schema")
            if entry.get("sequence") != sequence:
                raise QuarantineExecutionError(
                    "quarantine journal sequence is not contiguous"
                )
            if entry.get("event") not in _QUARANTINE_EVENTS:
                raise QuarantineExecutionError(
                    "quarantine journal contains an unknown event"
                )
            if entry.get("quarantine_plan_sha256") != self.prepared.plan.sha256:
                raise QuarantineExecutionError(
                    "quarantine journal belongs to another quarantine plan"
                )
            if entry.get("plan_sha256") != self.prepared.plan.plan_sha256:
                raise QuarantineExecutionError("quarantine journal belongs to another plan")
            if (
                entry.get("review_session_sha256")
                != self.prepared.plan.review_session_sha256
            ):
                raise QuarantineExecutionError(
                    "quarantine journal belongs to another review"
                )
            if entry.get("source_revision") != self.prepared.plan.source_revision:
                raise QuarantineExecutionError(
                    "quarantine journal belongs to another revision"
                )
            entries.append(entry)
        if not entries:
            raise QuarantineExecutionError("resume quarantine journal is empty")
        return entries

    def append(
        self,
        event: str,
        *,
        group_id: str | None = None,
        member: QuarantineMember | None = None,
        directory: str | None = None,
        result: str,
        detail: str | None = None,
        recovery: str | None = None,
    ) -> None:
        entry: dict[str, object] = {
            "schema_version": QUARANTINE_JOURNAL_SCHEMA_VERSION,
            "sequence": len(self.entries) + 1,
            "timestamp_utc": _timestamp(),
            "quarantine_plan_sha256": self.prepared.plan.sha256,
            "plan_sha256": self.prepared.plan.plan_sha256,
            "review_session_sha256": self.prepared.plan.review_session_sha256,
            "source_revision": self.prepared.plan.source_revision,
            "event": event,
            "group_id": group_id,
            "role": member.role.value if member else None,
            "source_relative_path": member.source_relative_path if member else None,
            "quarantine_relative_path": member.source_relative_path if member else None,
            "directory": directory,
            "pre_state": (
                {
                    "source": "present-and-fingerprint-matched",
                    "quarantine": "absent",
                    "size": member.fingerprint.size,
                    "mtime_ns": member.fingerprint.mtime_ns,
                    "sha256": member.fingerprint.sha256,
                }
                if member
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
            raise QuarantineExecutionError(
                "could not durably append the quarantine journal"
            ) from exc
        self.entries.append(entry)

    def state(self) -> _JournalState:
        return _journal_state(self.entries, completion_event="run-completed")


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
            raise QuarantineExecutionError("restore journal already exists; use --resume")

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
                raise QuarantineExecutionError("restore journal sequence is not contiguous")
            if entry.get("event") not in _RESTORE_EVENTS:
                raise QuarantineExecutionError("restore journal contains an unknown event")
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


def _journal_state(
    entries: list[dict[str, object]], *, completion_event: str
) -> _JournalState:
    completed_groups: set[str] = set()
    completed_members: set[tuple[str, str]] = set()
    started_members: set[tuple[str, str]] = set()
    run_completed = False
    for entry in entries:
        event = cast(str, entry["event"])
        group_id = entry.get("group_id")
        source = entry.get("source_relative_path")
        key = (
            (cast(str, group_id), cast(str, source).replace("\\", "/").casefold())
            if isinstance(group_id, str) and isinstance(source, str)
            else None
        )
        if event == "member-started" and key is not None:
            started_members.add(key)
        elif event == "member-completed" and key is not None:
            completed_members.add(key)
            started_members.discard(key)
        elif event == "member-rollback-completed" and key is not None:
            completed_members.discard(key)
            started_members.discard(key)
        elif event == "group-completed" and isinstance(group_id, str):
            completed_groups.add(group_id)
        elif event == completion_event:
            run_completed = True
    return _JournalState(
        completed_groups=frozenset(completed_groups),
        completed_members=frozenset(completed_members),
        started_members=frozenset(started_members),
        run_completed=run_completed,
    )


def _ensure_quarantine_parents(
    member: QuarantineMember,
    quarantine_root: Path,
    journal: _QuarantineJournal,
    group_id: str,
) -> None:
    current = quarantine_root
    for part in _parts(member.source_relative_path)[:-1]:
        current = current / part
        relative = current.relative_to(quarantine_root).as_posix()
        if os.path.lexists(current):
            if _is_linklike(current) or not current.is_dir():
                raise QuarantineExecutionError(
                    "quarantine parent became a link or non-directory"
                )
            continue
        journal.append(
            "directory-create-started",
            group_id=group_id,
            directory=relative,
            result="started",
        )
        try:
            current.mkdir()
        except OSError as exc:
            raise QuarantineExecutionError(
                "quarantine directory creation failed"
            ) from exc
        journal.append(
            "directory-created",
            group_id=group_id,
            directory=relative,
            result="completed",
            recovery="empty quarantine directories are intentionally retained",
        )


def _validate_pending_member(
    member: QuarantineMember, source_root: Path, quarantine_root: Path
) -> None:
    if _member_state(member, source_root, quarantine_root) != "pending":
        raise QuarantineExecutionError("duplicate loser is not in pending source state")
    source = _candidate_path(source_root, member.source_relative_path, require_parent=True)
    parent = quarantine_root
    for part in _parts(member.source_relative_path)[:-1]:
        candidate = parent / part
        if not os.path.lexists(candidate):
            break
        parent = candidate
    if source.stat().st_dev != parent.stat().st_dev:
        raise QuarantineExecutionError(
            "cross-filesystem duplicate quarantine remains disabled"
        )


def _validate_check(
    prepared: PreparedQuarantine,
    source_root: Path,
    organized_root: Path,
    quarantine_root: Path,
) -> tuple[int, int]:
    preapply = 0
    organized = 0
    for group in prepared.plan.groups:
        winner = _winner_state(group, source_root, organized_root)
        if winner == "preapply":
            preapply += 1
        else:
            organized += 1
        for member in group.members:
            _validate_pending_member(member, source_root, quarantine_root)
    return preapply, organized


def _rollback_quarantined_members(
    group: QuarantineGroup,
    moved: list[QuarantineMember],
    source_root: Path,
    quarantine_root: Path,
    journal: _QuarantineJournal,
) -> list[str]:
    failures: list[str] = []
    for member in reversed(moved):
        try:
            if _member_state(member, source_root, quarantine_root) != "quarantined":
                raise QuarantineExecutionError("quarantine rollback state is ambiguous")
            journal.append(
                "member-rollback-started",
                group_id=group.group_id,
                member=member,
                result="started",
            )
            source = _candidate_path(
                source_root, member.source_relative_path, require_parent=True
            )
            quarantined = _candidate_path(
                quarantine_root, member.source_relative_path, require_parent=True
            )
            _atomic_rename_no_replace(quarantined, source)
            if _member_state(member, source_root, quarantine_root) != "pending":
                raise QuarantineExecutionError(
                    "quarantine rollback verification failed"
                )
            journal.append(
                "member-rollback-completed",
                group_id=group.group_id,
                member=member,
                result="completed",
            )
        except (OSError, ApplyExecutionError, QuarantineExecutionError) as exc:
            failures.append(
                f"{member.source_relative_path}: {exc}; leave both paths untouched "
                "and restore this member manually before --resume"
            )
    return failures


def execute_quarantine(
    prepared: PreparedQuarantine,
    source_root: Path,
    organized_root: Path,
    quarantine_root: Path,
    *,
    journal_path: Path | None,
    check_only: bool = False,
    resume: bool = False,
) -> QuarantineExecutionResult:
    """Check or atomically move reviewed duplicate losers outside the library."""

    roots = _validate_roots(source_root, organized_root, quarantine_root)
    source_root, organized_root, quarantine_root = roots
    members_total = sum(len(group.members) for group in prepared.plan.groups)
    if check_only:
        if resume:
            raise QuarantineExecutionError("--check-only cannot be combined with --resume")
        preapply, organized = _validate_check(
            prepared, source_root, organized_root, quarantine_root
        )
        return QuarantineExecutionResult(
            quarantine_plan_sha256=prepared.plan.sha256,
            plan_sha256=prepared.plan.plan_sha256,
            review_session_sha256=prepared.plan.review_session_sha256,
            groups_total=len(prepared.plan.groups),
            members_total=members_total,
            winners_preapply=preapply,
            winners_organized=organized,
            groups_completed=0,
            members_moved=0,
            members_recovered=0,
            journal_path=None,
            check_only=True,
        )
    if journal_path is None:
        raise QuarantineExecutionError(
            "duplicate quarantine requires an explicit journal path"
        )
    journal_path = _validate_journal_location(
        journal_path, roots, label="quarantine journal"
    )
    lock = _journal_lock(journal_path)
    moved_count = 0
    recovered_count = 0
    try:
        journal = _QuarantineJournal(journal_path, prepared, resume=resume)
        if not resume:
            preapply, organized = _validate_check(
                prepared, source_root, organized_root, quarantine_root
            )
            if preapply:
                raise QuarantineExecutionError(
                    "duplicate quarantine cannot start until every reviewed winner is "
                    "at its approved organized destination"
                )
            if organized != len(prepared.plan.groups):
                raise QuarantineExecutionError("not every duplicate winner is organized")
            journal.append("run-started", result="started")
        state = journal.state()
        if state.run_completed:
            for group in prepared.plan.groups:
                if _winner_state(group, source_root, organized_root) != "organized":
                    raise QuarantineExecutionError(
                        "completed quarantine winner state no longer matches"
                    )
                for member in group.members:
                    if _member_state(member, source_root, quarantine_root) != "quarantined":
                        raise QuarantineExecutionError(
                            "completed quarantine member state no longer matches"
                        )
            return QuarantineExecutionResult(
                quarantine_plan_sha256=prepared.plan.sha256,
                plan_sha256=prepared.plan.plan_sha256,
                review_session_sha256=prepared.plan.review_session_sha256,
                groups_total=len(prepared.plan.groups),
                members_total=members_total,
                winners_preapply=0,
                winners_organized=len(prepared.plan.groups),
                groups_completed=len(prepared.plan.groups),
                members_moved=0,
                members_recovered=0,
                journal_path=journal_path,
                check_only=False,
            )

        completed_groups = set(state.completed_groups)
        for group in prepared.plan.groups:
            if group.group_id in completed_groups:
                if _winner_state(group, source_root, organized_root) != "organized":
                    raise QuarantineExecutionError(
                        "reviewed winner changed after duplicate quarantine"
                    )
                for member in group.members:
                    if _member_state(member, source_root, quarantine_root) != "quarantined":
                        raise QuarantineExecutionError(
                            "completed quarantine group no longer matches filesystem"
                        )
                continue
            if _winner_state(group, source_root, organized_root) != "organized":
                raise QuarantineExecutionError(
                    "reviewed winner is not at its approved organized destination"
                )
            current = journal.state()
            pending: list[QuarantineMember] = []
            recovered: list[QuarantineMember] = []
            for member in group.members:
                key = _member_key(group.group_id, member)
                member_state = _member_state(member, source_root, quarantine_root)
                if key in current.completed_members:
                    if member_state != "quarantined":
                        raise QuarantineExecutionError(
                            "quarantine journal and filesystem disagree"
                        )
                    recovered.append(member)
                    continue
                if key in current.started_members and member_state == "quarantined":
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
                        "duplicate loser changed without matching journal evidence"
                    )
                pending.append(member)

            journal.append(
                "group-started",
                group_id=group.group_id,
                result="resumed" if recovered else "started",
            )
            moved_this_attempt: list[QuarantineMember] = []
            try:
                for member in pending:
                    _validate_pending_member(member, source_root, quarantine_root)
                for member in pending:
                    _ensure_quarantine_parents(
                        member, quarantine_root, journal, group.group_id
                    )
                    journal.append(
                        "member-started",
                        group_id=group.group_id,
                        member=member,
                        result="started",
                    )
                    if _member_state(member, source_root, quarantine_root) != "pending":
                        raise QuarantineExecutionError(
                            "duplicate loser changed immediately before quarantine"
                        )
                    source = _candidate_path(
                        source_root, member.source_relative_path, require_parent=True
                    )
                    quarantined = _candidate_path(
                        quarantine_root, member.source_relative_path, require_parent=True
                    )
                    _atomic_rename_no_replace(source, quarantined)
                    if _member_state(member, source_root, quarantine_root) != "quarantined":
                        raise QuarantineExecutionError(
                            "quarantine destination verification failed"
                        )
                    moved_this_attempt.append(member)
                    journal.append(
                        "member-completed",
                        group_id=group.group_id,
                        member=member,
                        result="completed",
                    )
                    moved_count += 1
                journal.append(
                    "group-completed",
                    group_id=group.group_id,
                    result="completed",
                )
                completed_groups.add(group.group_id)
            except (Exception, KeyboardInterrupt) as exc:
                rollback_failures = _rollback_quarantined_members(
                    group,
                    moved_this_attempt,
                    source_root,
                    quarantine_root,
                    journal,
                )
                recovery = (
                    "leave quarantined files and source files untouched; inspect the "
                    "reported group and use --resume only after restoring an unambiguous "
                    "pending or journal-supported quarantined state"
                )
                try:
                    journal.append(
                        "group-failed",
                        group_id=group.group_id,
                        result="incomplete",
                        detail=(
                            f"{type(exc).__name__}: {exc}; rollback_failures="
                            + (" | ".join(rollback_failures) if rollback_failures else "none")
                        ),
                        recovery=recovery,
                    )
                except QuarantineExecutionError:
                    pass
                if isinstance(exc, QuarantineExecutionError):
                    raise
                raise QuarantineExecutionError(
                    f"duplicate quarantine interrupted safely: {exc}; {recovery}"
                ) from exc

        for group in prepared.plan.groups:
            if _winner_state(group, source_root, organized_root) != "organized":
                raise QuarantineExecutionError(
                    "post-quarantine winner verification failed"
                )
            for member in group.members:
                if _member_state(member, source_root, quarantine_root) != "quarantined":
                    raise QuarantineExecutionError(
                        "post-quarantine loser verification failed"
                    )
        journal.append("run-completed", result="completed")
        return QuarantineExecutionResult(
            quarantine_plan_sha256=prepared.plan.sha256,
            plan_sha256=prepared.plan.plan_sha256,
            review_session_sha256=prepared.plan.review_session_sha256,
            groups_total=len(prepared.plan.groups),
            members_total=members_total,
            winners_preapply=0,
            winners_organized=len(prepared.plan.groups),
            groups_completed=len(completed_groups),
            members_moved=moved_count,
            members_recovered=recovered_count,
            journal_path=journal_path,
            check_only=False,
        )
    except ApplyExecutionError as exc:
        raise QuarantineExecutionError(str(exc)) from exc
    finally:
        _release_journal_lock(lock)


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
            raise QuarantineExecutionError("--check-only cannot be combined with --resume")
        _restore_all_state(prepared, source_root, quarantine_root, "pending")
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
                    if _restore_state(member, source_root, quarantine_root) != "restored":
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
                        quarantine_root, member.source_relative_path, require_parent=True
                    )
                    if _restore_state(member, source_root, quarantine_root) != "pending":
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
                    if _restore_state(member, source_root, quarantine_root) != "restored":
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
