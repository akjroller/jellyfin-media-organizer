from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .models import SourceFile, SourceFingerprint
from .paths import is_link

# Single-file video containers JMO plans.  The container never affects
# matching: duplicates are grouped by provider episode identity, so an .mkv and
# an .m4v of the same episode go through the normal reviewed-duplicate path.
VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".ts",
        ".webm",
        ".wmv",
    }
)
# Video files Jellyfin can play that JMO does not plan yet: disc images and
# DVD streams (one file is not one episode), and rarer containers.  They are
# reported as skipped instead of silently disappearing from the inventory.
UNSUPPORTED_VIDEO_EXTENSIONS = frozenset(
    {
        ".3gp",
        ".asf",
        ".divx",
        ".flv",
        ".iso",
        ".m2v",
        ".ogm",
        ".ogv",
        ".rm",
        ".rmvb",
        ".vob",
        ".wtv",
    }
)
# Blu-ray and DVD folder structures hold stream pieces, not episodes.  Any
# video file below one of these folders is reported as skipped.
DISC_STRUCTURE_DIRECTORY_NAMES = frozenset({"bdmv", "video_ts", "hvdvd_ts"})
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
    UNSUPPORTED_VIDEO = "unsupported-video"
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


def authorize_shows_root(path: Path) -> AuthorizedShowsRoot:
    candidate = path.expanduser()
    if is_link(candidate):
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


def _inside_disc_structure(root: Path, path: Path) -> bool:
    relative_parents = path.relative_to(root).parts[:-1]
    return any(
        part.casefold() in DISC_STRUCTURE_DIRECTORY_NAMES for part in relative_parents
    )


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


SKIPPED_INVENTORY_STATUSES = frozenset(
    {InventoryStatus.EXCLUDED_SAMPLE, InventoryStatus.UNSUPPORTED_VIDEO}
)


def skipped_inventory_records(
    inventory: tuple[InventoryRecord, ...],
) -> tuple[InventoryRecord, ...]:
    """Return video-like entries that are deliberately left out of the plan."""

    return tuple(
        record for record in inventory if record.status in SKIPPED_INVENTORY_STATUSES
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

            if is_link(path, entry_is_symlink=entry_is_symlink):
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

            if extension not in VIDEO_EXTENSIONS or _inside_disc_structure(
                root.path, path
            ):
                if (
                    extension in VIDEO_EXTENSIONS
                    or extension in UNSUPPORTED_VIDEO_EXTENSIONS
                ):
                    records.append(
                        InventoryRecord(
                            relative_path=path.relative_to(root.path).as_posix(),
                            extension=extension,
                            status=InventoryStatus.UNSUPPORTED_VIDEO,
                            fingerprint=None,
                            reason=(
                                f"disc-structure:{extension}"
                                if extension in VIDEO_EXTENSIONS
                                else f"unsupported-extension:{extension}"
                            ),
                        )
                    )
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
