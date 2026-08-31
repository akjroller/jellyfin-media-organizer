from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .models import SourceFile, SourceFingerprint

VIDEO_EXTENSIONS = frozenset({".avi", ".mkv", ".mp4"})
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        "artwork",
        "metadata",
        "movies",
        "quarantine",
        "subtitles",
    }
)
_SAMPLE_TOKEN = re.compile(r"(?:^|[ ._-])sample(?:$|[ ._-])", re.IGNORECASE)


class InventoryStatus(StrEnum):
    INCLUDED = "included"
    EXCLUDED_SAMPLE = "excluded-sample"
    BLOCKED_LINK = "blocked-link"
    UNREADABLE = "unreadable"


class InventoryScanError(RuntimeError):
    """Raised when a complete, deterministic inventory cannot be produced."""


@dataclass(frozen=True, slots=True)
class AuthorizedShowsRoot:
    path: Path


@dataclass(frozen=True, slots=True)
class InventoryRecord:
    relative_path: str
    extension: str
    status: InventoryStatus
    fingerprint: SourceFingerprint | None
    reason: str | None = None

    def to_source_file(self) -> SourceFile:
        if self.status is not InventoryStatus.INCLUDED or self.fingerprint is None:
            raise ValueError("only included inventory records are source files")
        return SourceFile(
            relative_path=self.relative_path,
            extension=self.extension,
            fingerprint=self.fingerprint,
        )


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return True


def _is_link(path: Path, *, entry_is_symlink: bool = False) -> bool:
    return entry_is_symlink or path.is_symlink() or _is_junction(path)


def authorize_shows_root(path: Path) -> AuthorizedShowsRoot:
    candidate = path.expanduser()
    if _is_link(candidate):
        raise InventoryScanError("Shows root itself cannot be a symlink or junction")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise InventoryScanError(f"Shows root is unavailable: {candidate}") from exc
    if not resolved.is_dir():
        raise InventoryScanError(f"Shows root is not a directory: {candidate}")
    return AuthorizedShowsRoot(path=resolved)


def _windows_path_key(relative_path: str) -> tuple[str, str]:
    return relative_path.casefold(), relative_path


def _is_sample(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if any(part.casefold() in {"sample", "samples"} for part in path.parts[:-1]):
        return True
    return bool(_SAMPLE_TOKEN.search(path.stem))


def _fingerprint(entry: os.DirEntry[str]) -> SourceFingerprint:
    stat = entry.stat(follow_symlinks=False)
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _blocked_link_record(root: Path, path: Path) -> InventoryRecord:
    relative_path = path.relative_to(root).as_posix()
    return InventoryRecord(
        relative_path=relative_path,
        extension=path.suffix.casefold(),
        status=InventoryStatus.BLOCKED_LINK,
        fingerprint=None,
        reason="symlink-or-junction",
    )


def scan_videos(root: AuthorizedShowsRoot) -> tuple[InventoryRecord, ...]:
    if not isinstance(root, AuthorizedShowsRoot):
        raise TypeError("scan_videos requires an explicitly authorized Shows root")

    records: list[InventoryRecord] = []

    def walk(directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError as exc:
            raise InventoryScanError(
                f"cannot enumerate directory: {directory}"
            ) from exc

        for entry in entries:
            path = Path(entry.path)
            extension = path.suffix.casefold()
            entry_is_symlink = entry.is_symlink()

            if _is_link(path, entry_is_symlink=entry_is_symlink):
                if extension in VIDEO_EXTENSIONS:
                    records.append(_blocked_link_record(root.path, path))
                continue

            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                if extension in VIDEO_EXTENSIONS:
                    records.append(
                        InventoryRecord(
                            relative_path=path.relative_to(root.path).as_posix(),
                            extension=extension,
                            status=InventoryStatus.UNREADABLE,
                            fingerprint=None,
                            reason=f"entry-type-error:{type(exc).__name__}",
                        )
                    )
                    continue
                raise InventoryScanError(f"cannot inspect entry: {path}") from exc

            if is_directory:
                if entry.name.casefold() not in IGNORED_DIRECTORY_NAMES:
                    walk(path)
                continue

            if extension not in VIDEO_EXTENSIONS:
                continue

            try:
                is_file = entry.is_file(follow_symlinks=False)
                fingerprint = _fingerprint(entry)
            except OSError as exc:
                records.append(
                    InventoryRecord(
                        relative_path=path.relative_to(root.path).as_posix(),
                        extension=extension,
                        status=InventoryStatus.UNREADABLE,
                        fingerprint=None,
                        reason=f"stat-error:{type(exc).__name__}",
                    )
                )
                continue

            if not is_file:
                records.append(
                    InventoryRecord(
                        relative_path=path.relative_to(root.path).as_posix(),
                        extension=extension,
                        status=InventoryStatus.UNREADABLE,
                        fingerprint=None,
                        reason="not-a-regular-file",
                    )
                )
                continue

            relative_path = path.relative_to(root.path).as_posix()
            status = (
                InventoryStatus.EXCLUDED_SAMPLE
                if _is_sample(relative_path)
                else InventoryStatus.INCLUDED
            )
            records.append(
                InventoryRecord(
                    relative_path=relative_path,
                    extension=extension,
                    status=status,
                    fingerprint=fingerprint,
                    reason=(
                        "sample-name"
                        if status is InventoryStatus.EXCLUDED_SAMPLE
                        else None
                    ),
                )
            )

    walk(root.path)
    records.sort(key=lambda record: _windows_path_key(record.relative_path))
    return tuple(records)
