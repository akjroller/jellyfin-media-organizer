from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .apply_contract import ApplyContract, ApplyMember, ApplyMemberRole
from .models import SourceFingerprint

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_HASH_CHUNK_SIZE = 1024 * 1024


class ApplyFilesystemError(RuntimeError):
    """Raised when live filesystem state no longer matches an approved apply contract."""


@dataclass(frozen=True, slots=True)
class ApplyMemberObservation:
    group_id: str
    role: ApplyMemberRole
    source_relative_path: str
    destination_relative_path: str
    fingerprint: SourceFingerprint
    source_device: int
    destination_device: int


@dataclass(frozen=True, slots=True)
class ApplyFilesystemValidation:
    plan_sha256: str
    observations: tuple[ApplyMemberObservation, ...]


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


def _validated_root(root: Path, label: str) -> Path:
    if not os.path.lexists(root):
        raise ApplyFilesystemError(f"{label} root does not exist")
    if _is_linklike(root):
        raise ApplyFilesystemError(f"{label} root cannot be a symlink or junction")
    if not root.is_dir():
        raise ApplyFilesystemError(f"{label} root must be a directory")
    return root.resolve(strict=True)


def _relative_parts(value: str, label: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", value.replace("\\", "/"))
    if not normalized or _WINDOWS_DRIVE.match(normalized):
        raise ApplyFilesystemError(f"{label} must be a safe relative path")
    raw_parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ApplyFilesystemError(f"{label} must not contain dot or empty segments")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts:
        raise ApplyFilesystemError(f"{label} must be a safe relative path")
    return tuple(path.parts)


def _existing_parent(
    root: Path,
    parts: tuple[str, ...],
    *,
    require_all: bool,
    label: str,
) -> Path:
    current = root
    for part in parts[:-1]:
        candidate = current / part
        if not os.path.lexists(candidate):
            if require_all:
                raise ApplyFilesystemError(f"{label} parent does not exist")
            return current
        if _is_linklike(candidate):
            raise ApplyFilesystemError(
                f"{label} parent chain contains a symlink or junction"
            )
        if not candidate.is_dir():
            raise ApplyFilesystemError(f"{label} parent chain contains a non-directory")
        current = candidate
    return current


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_fingerprint(path: Path, expected: SourceFingerprint) -> SourceFingerprint:
    before = path.stat()
    if before.st_size != expected.size or before.st_mtime_ns != expected.mtime_ns:
        raise ApplyFilesystemError("source fingerprint size/mtime no longer matches plan")

    sha256: str | None = None
    if expected.sha256 is not None:
        sha256 = _hash_file(path)
        after = path.stat()
        if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            raise ApplyFilesystemError("source changed while fingerprint was being verified")
        if sha256 != expected.sha256:
            raise ApplyFilesystemError("source SHA-256 no longer matches plan")

    return SourceFingerprint(
        size=before.st_size,
        mtime_ns=before.st_mtime_ns,
        sha256=sha256,
    )


def revalidate_apply_member(
    group_id: str,
    member: ApplyMember,
    source_root: Path,
    destination_root: Path,
) -> ApplyMemberObservation:
    """Read live state immediately before a future move without mutating anything."""

    source_root = _validated_root(source_root, "source")
    destination_root = _validated_root(destination_root, "destination")
    source_parts = _relative_parts(member.source_relative_path, "source")
    destination_parts = _relative_parts(member.destination_relative_path, "destination")

    _existing_parent(
        source_root,
        source_parts,
        require_all=True,
        label="source",
    )
    source = source_root.joinpath(*source_parts)
    if not os.path.lexists(source):
        raise ApplyFilesystemError("source does not exist")
    if _is_linklike(source):
        raise ApplyFilesystemError("source cannot be a symlink or junction")
    if not source.is_file():
        raise ApplyFilesystemError("source must be a regular file")

    destination_parent = _existing_parent(
        destination_root,
        destination_parts,
        require_all=False,
        label="destination",
    )
    destination = destination_root.joinpath(*destination_parts)
    if os.path.lexists(destination):
        raise ApplyFilesystemError("destination already exists")

    fingerprint = _validate_fingerprint(source, member.fingerprint)
    source_device = source.stat().st_dev
    destination_device = destination_parent.stat().st_dev
    if source_device != destination_device:
        raise ApplyFilesystemError("cross-filesystem apply remains disabled")

    return ApplyMemberObservation(
        group_id=group_id,
        role=member.role,
        source_relative_path=member.source_relative_path,
        destination_relative_path=member.destination_relative_path,
        fingerprint=fingerprint,
        source_device=source_device,
        destination_device=destination_device,
    )


def revalidate_apply_contract(
    contract: ApplyContract,
    source_root: Path,
    destination_root: Path,
) -> ApplyFilesystemValidation:
    """Revalidate every moving member in deterministic operation-group order."""

    observations = tuple(
        revalidate_apply_member(group.group_id, member, source_root, destination_root)
        for group in contract.groups
        for member in group.moving_members
    )
    return ApplyFilesystemValidation(
        plan_sha256=contract.plan_sha256,
        observations=observations,
    )
