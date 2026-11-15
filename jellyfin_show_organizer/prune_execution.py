"""Remove source folders that a completed apply left empty, and put them back.

Only folders that held files moved by one completed apply journal, and their
parents below the Shows root, are candidates.  A folder is removed only when it
is truly empty, deepest first, with ``os.rmdir``, which the operating system
refuses for any folder that is not empty.  Every removal is journaled so
``restore_pruned_dirs`` can recreate the exact folders before a rollback needs
them again.  Media files are never touched.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .paths import is_link

PRUNE_JOURNAL_SCHEMA_VERSION = 1
_APPLY_JOURNAL_SCHEMA_VERSION = 1


class PruneError(RuntimeError):
    """Raised when empty-folder cleanup cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class PrunePlan:
    source_root: Path
    apply_journal_sha256: str
    directories: tuple[str, ...]

    @property
    def token(self) -> str:
        payload = json.dumps(
            {
                "root": str(self.source_root),
                "apply_journal_sha256": self.apply_journal_sha256,
                "directories": list(self.directories),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"PRUNE:{digest}"


@dataclass(frozen=True, slots=True)
class PruneResult:
    directories: tuple[str, ...]
    removed: tuple[str, ...]
    journal_path: Path | None
    check_only: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "check_only": self.check_only,
            "directories": list(self.directories),
            "removed": list(self.removed),
            "journal_path": str(self.journal_path) if self.journal_path else None,
        }


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PruneError("apply journal is unreadable") from exc


def _validated_root(source_root: Path) -> Path:
    candidate = source_root.expanduser()
    if is_link(candidate):
        raise PruneError("source root cannot be a symlink or junction")
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise PruneError("source root does not exist") from exc
    if not root.is_dir():
        raise PruneError("source root must be a directory")
    return root


def _relative_parts(value: str) -> tuple[str, ...]:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise PruneError("journal contains an unsafe relative path")
    return path.parts


def _moved_sources(apply_journal: Path) -> tuple[str, ...]:
    """Return source paths a completed apply journal moved and did not restore."""

    if is_link(apply_journal):
        raise PruneError("apply journal cannot be a symlink")
    try:
        lines = apply_journal.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PruneError("apply journal is unreadable") from exc
    moved: dict[str, str] = {}
    plan_sha256: object = None
    last_event: object = None
    for sequence, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PruneError("apply journal contains invalid JSON") from exc
        if not isinstance(entry, dict):
            raise PruneError("apply journal entries must be objects")
        if entry.get("schema_version") != _APPLY_JOURNAL_SCHEMA_VERSION:
            raise PruneError("unsupported apply journal schema")
        if entry.get("sequence") != sequence:
            raise PruneError("apply journal sequence is not contiguous")
        if plan_sha256 is None:
            plan_sha256 = entry.get("plan_sha256")
        elif entry.get("plan_sha256") != plan_sha256:
            raise PruneError("apply journal mixes plans")
        event = entry.get("event")
        source = entry.get("source_relative_path")
        if isinstance(source, str):
            key = source.replace("\\", "/").casefold()
            if event == "member-completed":
                moved[key] = source
            elif event == "member-rollback-completed":
                moved.pop(key, None)
        last_event = event
    if last_event != "run-completed":
        raise PruneError("empty-folder cleanup requires a completed apply journal")
    return tuple(sorted(moved.values(), key=lambda value: (value.casefold(), value)))


def _candidate_directories(sources: Iterable[str]) -> tuple[str, ...]:
    candidates: dict[str, str] = {}
    for source in sources:
        parts = _relative_parts(source)
        for depth in range(len(parts) - 1, 0, -1):
            relative = "/".join(parts[:depth])
            candidates.setdefault(relative.casefold(), relative)
    return tuple(candidates.values())


def _safe_directory(root: Path, relative: str) -> Path | None:
    """Return the folder if it exists as a real directory chain, else None."""

    current = root
    for part in _relative_parts(relative):
        current = current / part
        if not os.path.lexists(current):
            return None
        if is_link(current) or not current.is_dir():
            raise PruneError(f"folder chain is not a plain directory: {relative}")
    return current


def _depth_first(directories: Iterable[str]) -> list[str]:
    return sorted(
        directories,
        key=lambda value: (-len(_relative_parts(value)), value.casefold(), value),
    )


def _removable(root: Path, candidates: tuple[str, ...]) -> tuple[str, ...]:
    """Folders that are empty once their removable child folders are gone."""

    removable: set[str] = set()
    for relative in _depth_first(candidates):
        directory = _safe_directory(root, relative)
        if directory is None:
            continue
        try:
            with os.scandir(directory) as entries:
                children = list(entries)
        except OSError as exc:
            raise PruneError(f"cannot inspect folder: {relative}") from exc
        if all(
            child.is_dir(follow_symlinks=False)
            and not child.is_symlink()
            and f"{relative}/{child.name}".casefold() in removable
            for child in children
        ):
            removable.add(relative.casefold())
    return tuple(
        relative
        for relative in _depth_first(candidates)
        if relative.casefold() in removable
    )


def plan_prune(source_root: Path, apply_journal: Path) -> PrunePlan:
    """List exactly which empty folders a cleanup would remove; change nothing."""

    root = _validated_root(source_root)
    journal = apply_journal.expanduser()
    sources = _moved_sources(journal)
    directories = _removable(root, _candidate_directories(sources))
    return PrunePlan(
        source_root=root,
        apply_journal_sha256=_sha256(journal),
        directories=directories,
    )


def _validate_journal_location(path: Path, root: Path, label: str) -> Path:
    if is_link(path) or is_link(path.parent):
        raise PruneError(f"{label} cannot be a symlink")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise PruneError(f"{label} parent does not exist") from exc
    if parent == root or parent.is_relative_to(root):
        raise PruneError(f"{label} must be outside the source root")
    return parent / path.name


def _append(journal: Path, entry: dict[str, object], *, create: bool) -> None:
    payload = (
        json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    try:
        with journal.open("xb" if create else "ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise PruneError("prune journal already exists") from exc
    except OSError as exc:
        raise PruneError("could not durably append the prune journal") from exc


def execute_prune(
    plan: PrunePlan,
    apply_journal: Path,
    *,
    confirm: str,
    journal_path: Path,
) -> PruneResult:
    """Remove the confirmed empty folders, deepest first, journaling each one."""

    if confirm != plan.token:
        raise PruneError("confirmation does not match the exact folder list and root")
    current = plan_prune(plan.source_root, apply_journal)
    if current != plan:
        raise PruneError("folders or apply journal changed since the check")
    journal = _validate_journal_location(journal_path, plan.source_root, "journal")
    if journal == apply_journal.expanduser().resolve(strict=False):
        raise PruneError("prune journal must be distinct from the apply journal")
    sequence = 0

    def record(event: str, **fields: object) -> None:
        nonlocal sequence
        sequence += 1
        _append(
            journal,
            {
                "schema_version": PRUNE_JOURNAL_SCHEMA_VERSION,
                "apply_journal_sha256": plan.apply_journal_sha256,
                "sequence": sequence,
                "event": event,
                "timestamp": _timestamp(),
                **fields,
            },
            create=sequence == 1,
        )

    record("prune-started")
    removed: list[str] = []
    for relative in plan.directories:
        directory = _safe_directory(plan.source_root, relative)
        if directory is None:
            continue
        try:
            # rmdir is refused by the OS for any folder that is not empty.
            os.rmdir(directory)
        except OSError as exc:
            record(
                "prune-stopped",
                directory=relative,
                detail=f"{type(exc).__name__}: {exc.strerror or exc}",
            )
            raise PruneError(
                f"stopped at {relative}: folder is no longer empty or removable; "
                "already removed folders are listed in the prune journal"
            ) from exc
        removed.append(relative)
        record("directory-removed", directory=relative)
    record("prune-completed")
    return PruneResult(
        directories=plan.directories,
        removed=tuple(removed),
        journal_path=journal,
        check_only=False,
    )


def _removed_directories(prune_journal: Path) -> tuple[str, ...]:
    if is_link(prune_journal):
        raise PruneError("prune journal cannot be a symlink")
    try:
        lines = prune_journal.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PruneError("prune journal is unreadable") from exc
    removed: list[str] = []
    for sequence, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PruneError("prune journal contains invalid JSON") from exc
        if not isinstance(entry, dict):
            raise PruneError("prune journal entries must be objects")
        if entry.get("schema_version") != PRUNE_JOURNAL_SCHEMA_VERSION:
            raise PruneError("unsupported prune journal schema")
        if entry.get("sequence") != sequence:
            raise PruneError("prune journal sequence is not contiguous")
        if entry.get("event") == "directory-removed":
            directory = entry.get("directory")
            if not isinstance(directory, str):
                raise PruneError("prune journal directory must be a string")
            _relative_parts(directory)
            removed.append(directory)
    if not lines:
        raise PruneError("prune journal is empty")
    return tuple(removed)


def restore_pruned_dirs(source_root: Path, prune_journal: Path) -> tuple[str, ...]:
    """Recreate every folder a prune journal removed, parents first.

    Folders that already exist are left as they are, so restoring twice is safe.
    """

    root = _validated_root(source_root)
    restored: list[str] = []
    for relative in reversed(_removed_directories(prune_journal.expanduser())):
        current = root
        for part in _relative_parts(relative):
            current = current / part
            if os.path.lexists(current):
                if is_link(current) or not current.is_dir():
                    raise PruneError(
                        f"cannot restore {relative}: a non-folder is in the way"
                    )
                continue
            try:
                current.mkdir()
            except OSError as exc:
                raise PruneError(f"could not recreate folder: {relative}") from exc
            restored.append(current.relative_to(root).as_posix())
    return tuple(restored)
