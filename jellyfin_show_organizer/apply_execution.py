from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import platform
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NoReturn, cast
from uuid import uuid4

from .apply_contract import (
    ApplyApproval,
    ApplyContract,
    ApplyContractError,
    ApplyMember,
    ApplyReviewApproval,
    ApplyReviewMode,
    build_apply_contract,
    derive_apply_group_ids,
    manifest_plan_hash,
)
from .apply_validation import (
    ApplyFilesystemError,
    revalidate_apply_contract,
    revalidate_apply_member,
    validate_apply_roots,
)
from .models import CacheSnapshot, SourceFingerprint

_HASH = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_JOURNAL_SCHEMA_VERSION = 1
_HASH_CHUNK_SIZE = 1024 * 1024
_EVENTS = frozenset(
    {
        "run-started",
        "directory-create-started",
        "directory-created",
        "group-started",
        "member-started",
        "member-completed",
        "member-rollback-started",
        "member-rollback-completed",
        "group-failed",
        "group-completed",
        "run-completed",
    }
)


class ApplyExecutionError(RuntimeError):
    """Raised when execution or recovery cannot continue safely."""


class UnsupportedRenameError(ApplyExecutionError):
    """The filesystem cannot perform an atomic rename that refuses to overwrite."""


UNSUPPORTED_RENAME_MESSAGE = (
    "this filesystem does not support atomic no-overwrite rename (common on "
    "NFS, SMB/CIFS, and FUSE network mounts); JMO cannot move media here safely. "
    "Run JMO on the machine that hosts the files, or use a local filesystem"
)
# errno values a filesystem or kernel uses to say "this rename flag is not
# supported" rather than "this particular rename failed".
_UNSUPPORTED_RENAME_ERRNOS = frozenset(
    value
    for value in (
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOTSUP", None),
    )
    if value is not None
)
_RENAME_PROBE_PREFIX = ".jmo-rename-probe-"


@dataclass(frozen=True, slots=True)
class PreparedApply:
    contract: ApplyContract
    review_session_sha256: str
    source_revision: str
    apply_scope_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ApplyExecutionResult:
    plan_sha256: str
    review_session_sha256: str
    groups_total: int
    groups_completed: int
    members_moved: int
    members_recovered: int
    journal_path: Path | None
    check_only: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "plan_sha256": self.plan_sha256,
            "review_session_sha256": self.review_session_sha256,
            "groups_total": self.groups_total,
            "groups_completed": self.groups_completed,
            "members_moved": self.members_moved,
            "members_recovered": self.members_recovered,
            "journal_path": (
                str(self.journal_path) if self.journal_path is not None else None
            ),
            "check_only": self.check_only,
        }


@dataclass(frozen=True, slots=True)
class _JournalState:
    completed_groups: frozenset[str]
    completed_members: frozenset[tuple[str, str, str]]
    started_members: frozenset[tuple[str, str, str]]
    run_completed: bool
    # Members whose automatic rollback started after their move completed but
    # whose rollback completion was never journaled (for example because the
    # journal device failed after the media was already restored).
    rollback_unfinished_members: frozenset[tuple[str, str, str]] = frozenset()


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ApplyExecutionError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ApplyExecutionError(f"{label} must be a non-empty string")
    return value


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApplyExecutionError(f"could not load valid {label}") from exc


def _cache_snapshots(manifest: Mapping[str, object]) -> tuple[CacheSnapshot, ...]:
    provenance = _mapping(manifest.get("provenance"), "plan provenance")
    raw = provenance.get("cache_snapshots")
    if not isinstance(raw, list | tuple):
        raise ApplyExecutionError("plan cache snapshots must be an array")
    snapshots: list[CacheSnapshot] = []
    for item in raw:
        entry = _mapping(item, "plan cache snapshot")
        snapshots.append(
            CacheSnapshot(
                provider=_string(entry.get("provider"), "cache provider"),
                kind=_string(entry.get("kind"), "cache kind"),
                request_key=_string(entry.get("request_key"), "cache request key"),
                snapshot_id=_string(entry.get("snapshot_id"), "cache snapshot id"),
                state=_string(entry.get("state"), "cache state"),
            )
        )
    return tuple(snapshots)


def prepare_apply(
    plan_path: Path,
    preflight_path: Path,
    run_provenance_path: Path,
    *,
    approved_plan_sha256: str,
    approved_review_session_sha256: str,
    approved_source_revision: str,
    separate_roots: bool = False,
) -> PreparedApply:
    """Bind exact approval values to one reviewed, clean, ready plan."""

    for value, label, pattern in (
        (approved_plan_sha256, "approved plan SHA-256", _HASH),
        (
            approved_review_session_sha256,
            "approved review-session SHA-256",
            _HASH,
        ),
        (approved_source_revision, "approved source revision", _REVISION),
    ):
        if pattern.fullmatch(value) is None:
            raise ApplyExecutionError(f"{label} has an invalid format")

    manifest = _load_json(plan_path, "plan manifest")
    preflight = _load_json(preflight_path, "preflight artifact")
    run_provenance = _load_json(run_provenance_path, "run provenance")
    manifest_root = _mapping(manifest, "plan manifest")
    run_root = _mapping(run_provenance, "run provenance")

    try:
        actual_plan_sha256 = manifest_plan_hash(manifest)
    except (ApplyContractError, ValueError) as exc:
        raise ApplyExecutionError(str(exc)) from exc
    if actual_plan_sha256 != approved_plan_sha256:
        raise ApplyExecutionError("approved plan SHA-256 does not match plan.json")
    if run_root.get("plan_sha256") != actual_plan_sha256:
        raise ApplyExecutionError("run provenance does not match the approved plan")

    source_revision = _mapping(
        run_root.get("source_revision"), "run provenance source revision"
    )
    if (
        source_revision.get("state") != "git"
        or source_revision.get("dirty") is not False
        or source_revision.get("commit") != approved_source_revision
    ):
        raise ApplyExecutionError(
            "approved source revision is not the clean revision recorded by the plan"
        )

    run_preflight = _mapping(run_root.get("preflight"), "run provenance preflight")
    provider = _mapping(run_root.get("provider"), "run provenance provider")
    if (
        run_preflight.get("ready") is not True
        or run_preflight.get("finding_count") != 0
    ):
        raise ApplyExecutionError("run provenance does not describe a ready plan")
    if provider.get("failure") is not False:
        raise ApplyExecutionError("provider failure provenance cannot be applied")

    review = _mapping(run_root.get("review"), "run provenance review")
    if review.get("session_sha256") != approved_review_session_sha256:
        raise ApplyExecutionError(
            "approved review-session SHA-256 does not match run provenance"
        )
    if review.get("scope_state") != ApplyReviewMode.COMPLETE.value:
        raise ApplyExecutionError("apply requires one complete review session")
    if review.get("approved_scope_refs") not in ([], ()):
        raise ApplyExecutionError("partial review scope cannot authorize apply")

    plan_provenance = _mapping(manifest_root.get("provenance"), "plan provenance")
    group_ids = derive_apply_group_ids(manifest, separate_roots=separate_roots)
    approval = ApplyApproval(
        plan_sha256=approved_plan_sha256,
        schema_version=cast(int, manifest_root.get("schema_version")),
        tool_version=_string(plan_provenance.get("tool_version"), "tool version"),
        config_snapshot_id=_string(
            plan_provenance.get("config_snapshot_id"), "config snapshot"
        ),
        overrides_snapshot_id=_string(
            plan_provenance.get("overrides_snapshot_id"), "override snapshot"
        ),
        cache_snapshots=_cache_snapshots(manifest_root),
        authorized_group_ids=group_ids,
        review=ApplyReviewApproval(
            session_sha256=approved_review_session_sha256,
            mode=ApplyReviewMode.COMPLETE,
        ),
    )
    try:
        contract = build_apply_contract(
            manifest,
            preflight,
            approval,
            run_provenance=run_provenance,
            separate_roots=separate_roots,
        )
    except (ApplyContractError, ValueError) as exc:
        raise ApplyExecutionError(str(exc)) from exc
    return PreparedApply(
        contract=contract,
        review_session_sha256=approved_review_session_sha256,
        source_revision=approved_source_revision,
    )


def approval_token(
    prepared: PreparedApply, source_root: Path, destination_root: Path
) -> str:
    """Return the exact confirmation token for a plan, review, revision, roots, and scope."""

    source, destination = validate_apply_roots(source_root, destination_root)
    roots = hashlib.sha256(
        (str(source) + "\0" + str(destination)).encode("utf-8")
    ).hexdigest()
    parts = [
        "APPLY",
        prepared.contract.plan_sha256,
        prepared.review_session_sha256,
        prepared.source_revision,
    ]
    if prepared.apply_scope_sha256 is not None:
        parts.extend(("SCOPE", prepared.apply_scope_sha256))
    parts.append(roots)
    return ":".join(parts)


def _member_key(group_id: str, member: ApplyMember) -> tuple[str, str, str]:
    return (
        group_id,
        member.source_relative_path.replace("\\", "/").casefold(),
        member.destination_relative_path.replace("\\", "/").casefold(),
    )


def _parts(value: str) -> tuple[str, ...]:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ApplyExecutionError("approved member contains an unsafe relative path")
    return tuple(path.parts)


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return True


def _safe_existing_path(root: Path, relative_path: str) -> Path:
    current = root
    parts = _parts(relative_path)
    for part in parts[:-1]:
        current = current / part
        if not os.path.lexists(current) or not current.is_dir():
            raise ApplyExecutionError("approved member parent is missing")
        if _is_linklike(current):
            raise ApplyExecutionError("approved member parent became a link")
    return current / parts[-1]


def _paths(
    member: ApplyMember, source_root: Path, destination_root: Path
) -> tuple[Path, Path]:
    return (
        source_root.joinpath(*_parts(member.source_relative_path)),
        destination_root.joinpath(*_parts(member.destination_relative_path)),
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _Journal:
    def __init__(self, path: Path, prepared: PreparedApply, *, resume: bool) -> None:
        self.path = path
        self.prepared = prepared
        self.entries: list[dict[str, object]] = []
        if resume:
            if not path.is_file():
                raise ApplyExecutionError("resume journal does not exist")
            self.entries = self._read()
        elif os.path.lexists(path):
            raise ApplyExecutionError("journal already exists; use --resume")

    def _read(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ApplyExecutionError("apply journal is unreadable") from exc
        for sequence, line in enumerate(lines, 1):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ApplyExecutionError(
                    "apply journal contains invalid JSON"
                ) from exc
            entry = dict(_mapping(raw, "apply journal entry"))
            if entry.get("schema_version") != _JOURNAL_SCHEMA_VERSION:
                raise ApplyExecutionError("unsupported apply journal schema")
            if entry.get("sequence") != sequence:
                raise ApplyExecutionError("apply journal sequence is not contiguous")
            if entry.get("event") not in _EVENTS:
                raise ApplyExecutionError("apply journal contains an unknown event")
            if entry.get("plan_sha256") != self.prepared.contract.plan_sha256:
                raise ApplyExecutionError("apply journal belongs to another plan")
            if (
                entry.get("review_session_sha256")
                != self.prepared.review_session_sha256
            ):
                raise ApplyExecutionError("apply journal belongs to another review")
            if entry.get("source_revision") != self.prepared.source_revision:
                raise ApplyExecutionError("apply journal belongs to another revision")
            if entry.get("apply_scope_sha256") != self.prepared.apply_scope_sha256:
                raise ApplyExecutionError(
                    "apply journal belongs to another apply scope"
                )
            entries.append(entry)
        if not entries:
            raise ApplyExecutionError("resume journal is empty")
        return entries

    def append(
        self,
        event: str,
        *,
        group_id: str | None = None,
        member: ApplyMember | None = None,
        destination_directory: str | None = None,
        result: str,
        detail: str | None = None,
        recovery: str | None = None,
    ) -> None:
        entry: dict[str, object] = {
            "schema_version": _JOURNAL_SCHEMA_VERSION,
            "sequence": len(self.entries) + 1,
            "timestamp_utc": _timestamp(),
            "plan_sha256": self.prepared.contract.plan_sha256,
            "review_session_sha256": self.prepared.review_session_sha256,
            "source_revision": self.prepared.source_revision,
            "apply_scope_sha256": self.prepared.apply_scope_sha256,
            "event": event,
            "group_id": group_id,
            "role": member.role.value if member is not None else None,
            "source_relative_path": (
                member.source_relative_path if member is not None else None
            ),
            "destination_relative_path": (
                member.destination_relative_path if member is not None else None
            ),
            "destination_directory": destination_directory,
            "pre_state": (
                {
                    "source": "present-and-fingerprint-matched",
                    "destination": "absent",
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
            raise ApplyExecutionError(
                "could not durably append the apply journal"
            ) from exc
        self.entries.append(entry)

    def state(self) -> _JournalState:
        completed_groups: set[str] = set()
        completed_members: set[tuple[str, str, str]] = set()
        started_members: set[tuple[str, str, str]] = set()
        rollback_unfinished: set[tuple[str, str, str]] = set()
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
                rollback_unfinished.discard(key)
            elif event == "member-rollback-started" and key is not None:
                if key in completed_members:
                    rollback_unfinished.add(key)
            elif event == "member-rollback-completed" and key is not None:
                completed_members.discard(key)
                started_members.discard(key)
                rollback_unfinished.discard(key)
            elif event == "group-completed" and isinstance(group_id, str):
                completed_groups.add(group_id)
            elif event == "run-completed":
                run_completed = True
        return _JournalState(
            completed_groups=frozenset(completed_groups),
            completed_members=frozenset(completed_members),
            started_members=frozenset(started_members),
            run_completed=run_completed,
            rollback_unfinished_members=frozenset(rollback_unfinished),
        )


def _fingerprint_matches(path: Path, expected: SourceFingerprint) -> bool:
    try:
        stat = path.stat(follow_symlinks=False)
        if not path.is_file() or path.is_symlink():
            return False
        if stat.st_size != expected.size or stat.st_mtime_ns != expected.mtime_ns:
            return False
        if expected.sha256 is None:
            return True
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
        after = path.stat(follow_symlinks=False)
        return (
            after.st_size == stat.st_size
            and after.st_mtime_ns == stat.st_mtime_ns
            and digest.hexdigest() == expected.sha256
        )
    except OSError:
        return False


def _verify_destination(
    member: ApplyMember, source_root: Path, destination_root: Path
) -> None:
    source = _safe_existing_path(source_root, member.source_relative_path)
    destination = _safe_existing_path(
        destination_root, member.destination_relative_path
    )
    if os.path.lexists(source):
        raise ApplyExecutionError("completed move still has a source path")
    if not _fingerprint_matches(destination, member.fingerprint):
        raise ApplyExecutionError("completed move destination fingerprint is invalid")


def _verify_source(
    member: ApplyMember, source_root: Path, destination_root: Path
) -> None:
    source = _safe_existing_path(source_root, member.source_relative_path)
    destination = _safe_existing_path(
        destination_root, member.destination_relative_path
    )
    if os.path.lexists(destination):
        raise ApplyExecutionError("rolled-back move still has a destination path")
    if not _fingerprint_matches(source, member.fingerprint):
        raise ApplyExecutionError("rolled-back source fingerprint is invalid")


def _rollback_evidently_finished(
    member: ApplyMember, source_root: Path, destination_root: Path
) -> bool:
    """Whether an unjournaled rollback provably restored ``member`` to its source.

    True only when the destination is absent and the source matches the
    approved fingerprint.  Any other state is left to the ordinary checks,
    which fail closed on ambiguity.
    """

    source = _safe_existing_path(source_root, member.source_relative_path)
    destination = _safe_existing_path(
        destination_root, member.destination_relative_path
    )
    return not os.path.lexists(destination) and _fingerprint_matches(
        source, member.fingerprint
    )


def _atomic_rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename ``source`` to ``destination``, never replacing a file.

    Raises :class:`UnsupportedRenameError` when the host or filesystem cannot
    provide that guarantee, and ``FileExistsError`` when ``destination`` exists.
    """

    if os.name == "nt":
        # MoveFileEx without MOVEFILE_REPLACE_EXISTING refuses to overwrite.
        os.rename(source, destination)
        return

    system = platform.system()
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if system == "Linux" and hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(-100, source_bytes, -100, destination_bytes, 1)
    elif system == "Darwin" and hasattr(libc, "renamex_np"):
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(source_bytes, destination_bytes, 0x00000004)
    else:
        raise UnsupportedRenameError(
            "this host has no supported atomic no-overwrite rename primitive; "
            + UNSUPPORTED_RENAME_MESSAGE
        )
    if result != 0:
        _raise_rename_errno(ctypes.get_errno(), destination)


def _raise_rename_errno(error: int, destination: Path) -> NoReturn:
    """Translate a failed rename syscall, naming unsupported filesystems clearly."""

    if error in _UNSUPPORTED_RENAME_ERRNOS:
        raise UnsupportedRenameError(
            f"{UNSUPPORTED_RENAME_MESSAGE} ({os.strerror(error)})"
        )
    raise OSError(error, os.strerror(error), str(destination))


def probe_no_replace_rename(directory: Path) -> None:
    """Prove that ``directory`` supports the rename every JMO mutation relies on.

    Inside a fresh, uniquely named scratch directory the probe renames one file
    to an unused name, then checks that renaming a second file onto that name is
    refused and leaves both files intact.  Only the probe's own scratch entries
    are created, and they are removed whether the probe passes or fails.  Media
    and every other entry in ``directory`` are never touched.
    """

    scratch = directory / f"{_RENAME_PROBE_PREFIX}{uuid4().hex}"
    try:
        scratch.mkdir()
    except OSError as exc:
        raise ApplyExecutionError(
            "could not create a temporary rename-probe directory in the "
            f"destination root ({type(exc).__name__}); check that it is writable"
        ) from exc
    moved = scratch / "moved"
    original = scratch / "original"
    blocker = scratch / "blocker"
    try:
        original.write_bytes(b"original")
        _atomic_rename_no_replace(original, moved)
        if os.path.lexists(original) or moved.read_bytes() != b"original":
            raise UnsupportedRenameError(
                "rename probe did not move its file; " + UNSUPPORTED_RENAME_MESSAGE
            )
        blocker.write_bytes(b"blocker")
        try:
            _atomic_rename_no_replace(blocker, moved)
        except FileExistsError:
            pass
        else:
            raise UnsupportedRenameError(
                "rename probe overwrote an existing file; " + UNSUPPORTED_RENAME_MESSAGE
            )
        if moved.read_bytes() != b"original" or blocker.read_bytes() != b"blocker":
            raise UnsupportedRenameError(
                "rename probe changed an existing file; " + UNSUPPORTED_RENAME_MESSAGE
            )
    except OSError as exc:
        raise ApplyExecutionError(
            f"rename probe failed in the destination root: {type(exc).__name__}: "
            f"{exc.strerror or exc}"
        ) from exc
    finally:
        for entry in (original, moved, blocker):
            try:
                entry.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            scratch.rmdir()
        except OSError:
            pass


def _ensure_parent_directories(
    member: ApplyMember,
    destination_root: Path,
    journal: _Journal,
    group_id: str,
) -> None:
    current = destination_root
    for part in _parts(member.destination_relative_path)[:-1]:
        current = current / part
        relative = current.relative_to(destination_root).as_posix()
        if os.path.lexists(current):
            if _is_linklike(current) or not current.is_dir():
                raise ApplyExecutionError(
                    "destination parent became a link or non-directory"
                )
            continue
        journal.append(
            "directory-create-started",
            group_id=group_id,
            destination_directory=relative,
            result="started",
        )
        try:
            current.mkdir()
        except OSError as exc:
            raise ApplyExecutionError("destination directory creation failed") from exc
        journal.append(
            "directory-created",
            group_id=group_id,
            destination_directory=relative,
            result="completed",
            recovery="empty directories are intentionally not removed automatically",
        )


def _journal_lock(path: Path) -> BinaryIO:
    lock_path = path.with_name(path.name + ".lock")
    try:
        if path.is_symlink() or lock_path.is_symlink():
            raise ApplyExecutionError("apply journal paths cannot be symlinks")
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            api = vars(msvcrt)
            windows_lock = cast(Callable[[int, int, int], object], api["locking"])
            lock_nonblocking = cast(int, api["LK_NBLCK"])
            windows_lock(handle.fileno(), lock_nonblocking, 1)
        else:
            import fcntl

            api = vars(fcntl)
            lock = cast(Callable[[int, int], object], api["flock"])
            lock_ex = cast(int, api["LOCK_EX"])
            lock_nb = cast(int, api["LOCK_NB"])
            lock(handle.fileno(), lock_ex | lock_nb)
    except OSError as exc:
        if "handle" in locals():
            handle.close()
        raise ApplyExecutionError(
            "apply journal lock already exists; verify no apply process is running"
        ) from exc
    except ApplyExecutionError:
        raise
    return handle


def _release_journal_lock(handle: BinaryIO) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            api = vars(msvcrt)
            unlock = cast(Callable[[int, int, int], object], api["locking"])
            unlock_mode = cast(int, api["LK_UNLCK"])
            unlock(handle.fileno(), unlock_mode, 1)
        else:
            import fcntl

            api = vars(fcntl)
            lock = cast(Callable[[int, int], object], api["flock"])
            lock_un = cast(int, api["LOCK_UN"])
            lock(handle.fileno(), lock_un)
    finally:
        handle.close()


def _rollback_members(
    group_id: str,
    members: list[ApplyMember],
    source_root: Path,
    destination_root: Path,
    journal: _Journal,
) -> list[str]:
    failures: list[str] = []
    for member in reversed(members):
        journal_failure: str | None = None
        try:
            source = _safe_existing_path(source_root, member.source_relative_path)
            destination = _safe_existing_path(
                destination_root, member.destination_relative_path
            )
            try:
                journal.append(
                    "member-rollback-started",
                    group_id=group_id,
                    member=member,
                    result="started",
                )
            except ApplyExecutionError as exc:
                journal_failure = str(exc)
            if os.path.lexists(source):
                raise ApplyExecutionError("rollback source path is no longer absent")
            if not _fingerprint_matches(destination, member.fingerprint):
                raise ApplyExecutionError("rollback destination fingerprint is invalid")
            _atomic_rename_no_replace(destination, source)
            _verify_source(member, source_root, destination_root)
            try:
                journal.append(
                    "member-rollback-completed",
                    group_id=group_id,
                    member=member,
                    result="completed",
                    detail=(
                        f"earlier rollback journal failure: {journal_failure}"
                        if journal_failure is not None
                        else None
                    ),
                )
            except ApplyExecutionError:
                # The media has already been restored and verified. Preserve that
                # safer state even when the journal device itself has failed.
                pass
        except (OSError, ApplyExecutionError) as exc:
            instruction = (
                f"verify and restore '{member.destination_relative_path}' to "
                f"'{member.source_relative_path}'"
            )
            failures.append(f"{member.source_relative_path}: {exc}; {instruction}")
    return failures


def _resume_group_members(
    group_id: str,
    members: tuple[ApplyMember, ...],
    journal: _Journal,
    source_root: Path,
    destination_root: Path,
) -> tuple[list[ApplyMember], list[ApplyMember], int]:
    """Split one group's members into verified-completed and still-pending.

    Interrupted states are adopted only when the filesystem proves them: a move
    whose completion was not journaled, or a rollback whose completion was not
    journaled.  Every other state falls through to the ordinary fail-closed
    verification.
    """

    completed: list[ApplyMember] = []
    pending: list[ApplyMember] = []
    recovered = 0
    state = journal.state()
    for member in members:
        key = _member_key(group_id, member)
        if key in state.rollback_unfinished_members and _rollback_evidently_finished(
            member, source_root, destination_root
        ):
            # The restore happened; only its journal record was lost.
            journal.append(
                "member-rollback-completed",
                group_id=group_id,
                member=member,
                result="recovered-after-interruption",
            )
            recovered += 1
            pending.append(member)
            continue
        if key in state.completed_members:
            _verify_destination(member, source_root, destination_root)
            completed.append(member)
            continue
        if key in state.started_members:
            source = _safe_existing_path(source_root, member.source_relative_path)
            destination = _safe_existing_path(
                destination_root, member.destination_relative_path
            )
            if not os.path.lexists(source) and _fingerprint_matches(
                destination, member.fingerprint
            ):
                journal.append(
                    "member-completed",
                    group_id=group_id,
                    member=member,
                    result="recovered-after-interruption",
                )
                completed.append(member)
                recovered += 1
                continue
        pending.append(member)
    return completed, pending, recovered


def execute_apply(
    prepared: PreparedApply,
    source_root: Path,
    destination_root: Path,
    *,
    journal_path: Path | None,
    check_only: bool = False,
    resume: bool = False,
) -> ApplyExecutionResult:
    """Validate or execute one exact reviewed contract with durable recovery state."""

    source_root, destination_root = validate_apply_roots(source_root, destination_root)
    contract = prepared.contract
    if contract.separate_roots is not None and contract.separate_roots != (
        source_root != destination_root
    ):
        raise ApplyExecutionError(
            "apply roots differ from the prepared operation scope"
        )
    if check_only:
        if resume:
            raise ApplyExecutionError("--check-only cannot be combined with --resume")
        revalidate_apply_contract(contract, source_root, destination_root)
        probe_no_replace_rename(destination_root)
        return ApplyExecutionResult(
            plan_sha256=contract.plan_sha256,
            review_session_sha256=prepared.review_session_sha256,
            groups_total=len(contract.groups),
            groups_completed=0,
            members_moved=0,
            members_recovered=0,
            journal_path=None,
            check_only=True,
        )
    if journal_path is None:
        raise ApplyExecutionError("media apply requires an explicit journal path")
    if journal_path.is_symlink():
        raise ApplyExecutionError("apply journal cannot be a symlink")
    journal_parent = journal_path.parent.resolve(strict=True)
    if journal_parent == source_root or journal_parent.is_relative_to(source_root):
        raise ApplyExecutionError("apply journal must be outside the source root")
    if journal_parent == destination_root or journal_parent.is_relative_to(
        destination_root
    ):
        raise ApplyExecutionError("apply journal must be outside the destination root")
    if journal_path.parent.is_symlink():
        raise ApplyExecutionError("apply journal parent cannot be a symlink")

    lock_handle = _journal_lock(journal_path)
    moved_count = 0
    recovered_count = 0
    try:
        probe_no_replace_rename(destination_root)
        journal = _Journal(journal_path, prepared, resume=resume)
        if not resume:
            revalidate_apply_contract(contract, source_root, destination_root)
            journal.append("run-started", result="started")

        state = journal.state()
        if state.run_completed:
            for group in contract.groups:
                for member in group.moving_members:
                    _verify_destination(member, source_root, destination_root)
            return ApplyExecutionResult(
                plan_sha256=contract.plan_sha256,
                review_session_sha256=prepared.review_session_sha256,
                groups_total=len(contract.groups),
                groups_completed=len(contract.groups),
                members_moved=0,
                members_recovered=0,
                journal_path=journal_path,
                check_only=False,
            )

        completed_groups = set(state.completed_groups)
        for group in contract.groups:
            if group.group_id in completed_groups:
                for member in group.moving_members:
                    _verify_destination(member, source_root, destination_root)
                continue

            completed, pending, recovered = _resume_group_members(
                group.group_id,
                group.moving_members,
                journal,
                source_root,
                destination_root,
            )
            recovered_count += recovered

            journal.append(
                "group-started",
                group_id=group.group_id,
                result="resumed" if completed else "started",
            )
            try:
                for member in pending:
                    revalidate_apply_member(
                        group.group_id, member, source_root, destination_root
                    )
                for member in pending:
                    _ensure_parent_directories(
                        member, destination_root, journal, group.group_id
                    )
                    revalidate_apply_member(
                        group.group_id, member, source_root, destination_root
                    )
                    source, destination = _paths(member, source_root, destination_root)
                    journal.append(
                        "member-started",
                        group_id=group.group_id,
                        member=member,
                        result="started",
                    )
                    _atomic_rename_no_replace(source, destination)
                    completed.append(member)
                    _verify_destination(member, source_root, destination_root)
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
                rollback_failures = _rollback_members(
                    group.group_id,
                    completed,
                    source_root,
                    destination_root,
                    journal,
                )
                detail = f"{type(exc).__name__}: {exc}"
                recovery = None
                if rollback_failures:
                    recovery = "; ".join(rollback_failures)
                journal.append(
                    "group-failed",
                    group_id=group.group_id,
                    result="rollback-incomplete"
                    if rollback_failures
                    else "rolled-back",
                    detail=detail,
                    recovery=recovery,
                )
                if rollback_failures:
                    assert recovery is not None
                    raise ApplyExecutionError(
                        "operation group failed and automatic rollback is incomplete; "
                        + recovery
                    ) from exc
                reason = f": {exc}" if isinstance(exc, UnsupportedRenameError) else ""
                raise ApplyExecutionError(
                    "operation group failed and was rolled back safely" + reason
                ) from exc

        for group in contract.groups:
            for member in group.moving_members:
                _verify_destination(member, source_root, destination_root)
        journal.append("run-completed", result="completed")
        return ApplyExecutionResult(
            plan_sha256=contract.plan_sha256,
            review_session_sha256=prepared.review_session_sha256,
            groups_total=len(contract.groups),
            groups_completed=len(completed_groups),
            members_moved=moved_count,
            members_recovered=recovered_count,
            journal_path=journal_path,
            check_only=False,
        )
    except (ApplyFilesystemError, OSError, ValueError) as exc:
        if isinstance(exc, ApplyExecutionError):
            raise
        raise ApplyExecutionError(str(exc)) from exc
    finally:
        _release_journal_lock(lock_handle)


def total_moving_members(prepared: PreparedApply) -> int:
    return sum(len(group.moving_members) for group in prepared.contract.groups)
