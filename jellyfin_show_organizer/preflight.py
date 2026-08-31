from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .inventory import AuthorizedShowsRoot
from .models import DuplicateDecision, SourceFingerprint


class PreflightStatus(StrEnum):
    MATCHED = "matched"
    NON_MOVING = "non-moving"
    SUSPICIOUS = "suspicious"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class AuthorizedDestinationRoot:
    path: Path


@dataclass(frozen=True, slots=True)
class PreflightRecord:
    record_id: str
    source_relative_path: str
    status: PreflightStatus
    operation_group_id: str | None = None
    provider_identity: str | None = None
    numbering_identity: str | None = None
    destination_relative_path: str | None = None
    source_fingerprint: SourceFingerprint | None = None
    duplicate: DuplicateDecision | None = None

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("preflight record_id cannot be empty")
        if not self.source_relative_path:
            raise ValueError("preflight source_relative_path cannot be empty")


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    code: str
    record_ids: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("preflight finding code cannot be empty")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "record_ids": list(self.record_ids),
            "group_ids": list(self.group_ids),
        }
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True, slots=True)
class PreflightResult:
    plan_hash: str
    findings: tuple[PreflightFinding, ...]

    @property
    def ready(self) -> bool:
        return not self.findings

    @property
    def blocked_group_ids(self) -> tuple[str, ...]:
        groups = {
            group_id
            for finding in self.findings
            for group_id in finding.group_ids
            if group_id
        }
        return tuple(sorted(groups, key=lambda value: (value.casefold(), value)))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "plan_hash": self.plan_hash,
            "ready": self.ready,
            "blocked_group_ids": list(self.blocked_group_ids),
            "findings": [finding.to_dict() for finding in self.findings],
        }


_FORBIDDEN_COMPONENT_CHARACTERS = frozenset('<>:"\\|?*')
_WINDOWS_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_PLAN_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return True


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or _is_junction(path)
    except OSError:
        return True


def authorize_destination_root(path: Path) -> AuthorizedDestinationRoot:
    candidate = path.expanduser()
    if _is_link(candidate):
        raise ValueError("destination root cannot be a symlink or junction")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("destination root is unavailable") from exc
    if not resolved.is_dir():
        raise ValueError("destination root is not a directory")
    return AuthorizedDestinationRoot(path=resolved)


def _record_ids(records: Iterable[PreflightRecord]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {record.record_id for record in records},
            key=lambda value: (value.casefold(), value),
        )
    )


def _group_ids(records: Iterable[PreflightRecord]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                record.operation_group_id
                for record in records
                if record.operation_group_id is not None
            },
            key=lambda value: (value.casefold(), value),
        )
    )


def _finding(
    code: str,
    records: Iterable[PreflightRecord] = (),
    *,
    detail: str | None = None,
) -> PreflightFinding:
    record_group = tuple(records)
    return PreflightFinding(
        code=code,
        record_ids=_record_ids(record_group),
        group_ids=_group_ids(record_group),
        detail=detail,
    )


def _finding_sort_key(finding: PreflightFinding) -> tuple[object, ...]:
    return (
        finding.code,
        finding.record_ids,
        finding.group_ids,
        finding.detail or "",
    )


def _relative_path_codes(
    value: str,
    *,
    prefix: str,
    max_path_length: int,
    max_component_length: int,
) -> tuple[str, ...]:
    codes: list[str] = []
    if not value:
        return (f"{prefix}-path-empty",)
    if "\\" in value:
        codes.append(f"{prefix}-path-noncanonical-separator")
    if value.startswith("/") or value.startswith("//") or _WINDOWS_DRIVE.match(value):
        codes.append(f"{prefix}-root-escape")

    parts = value.split("/")
    if any(part == "" for part in parts):
        codes.append(f"{prefix}-path-empty-component")
    if any(part == "." for part in parts):
        codes.append(f"{prefix}-path-dot-component")
    if any(part == ".." for part in parts):
        codes.append(f"{prefix}-root-escape")

    if len(value) > max_path_length:
        codes.append(f"{prefix}-path-too-long")

    for part in parts:
        if part in {"", ".", ".."}:
            continue
        if len(part) > max_component_length:
            codes.append(f"{prefix}-component-too-long")
        if unicodedata.normalize("NFC", part) != part:
            codes.append(f"{prefix}-component-not-nfc")
        if any(character in _FORBIDDEN_COMPONENT_CHARACTERS for character in part):
            codes.append(f"{prefix}-component-forbidden-character")
        if any(ord(character) < 32 for character in part):
            codes.append(f"{prefix}-component-control-character")
        if part.endswith((" ", ".")):
            codes.append(f"{prefix}-component-trailing-dot-space")
        basename = part.split(".", 1)[0].rstrip(" .").casefold()
        if basename in _WINDOWS_RESERVED:
            codes.append(f"{prefix}-component-windows-reserved")

    return tuple(dict.fromkeys(codes))


def _candidate_path(root: Path, relative_path: str) -> Path:
    return root.joinpath(*relative_path.split("/"))


def _path_traverses_link(root: Path, relative_path: str) -> bool:
    current = root
    for part in relative_path.split("/"):
        if part in {"", ".", ".."}:
            continue
        current = current / part
        if _is_link(current):
            return True
    return False


def _resolved_inside(root: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return False
    return resolved == root or resolved.is_relative_to(root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_source_state(
    record: PreflightRecord,
    source_root: AuthorizedShowsRoot,
) -> tuple[PreflightFinding, ...]:
    fingerprint = record.source_fingerprint
    if fingerprint is None:
        return (_finding("matched-source-fingerprint-missing", (record,)),)

    codes = _relative_path_codes(
        record.source_relative_path,
        prefix="source",
        max_path_length=32_767,
        max_component_length=255,
    )
    findings = [_finding(code, (record,)) for code in codes]
    if codes:
        return tuple(findings)

    candidate = _candidate_path(source_root.path, record.source_relative_path)
    if _path_traverses_link(source_root.path, record.source_relative_path):
        findings.append(_finding("source-link-or-junction", (record,)))
        return tuple(findings)
    if not _resolved_inside(source_root.path, candidate):
        findings.append(_finding("source-root-escape", (record,)))
        return tuple(findings)
    try:
        stat = candidate.stat(follow_symlinks=False)
    except OSError:
        findings.append(_finding("source-missing-or-unreadable", (record,)))
        return tuple(findings)
    if not candidate.is_file():
        findings.append(_finding("source-not-regular-file", (record,)))
        return tuple(findings)

    changed = stat.st_size != fingerprint.size or stat.st_mtime_ns != fingerprint.mtime_ns
    if not changed and fingerprint.sha256 is not None:
        try:
            changed = _sha256(candidate) != fingerprint.sha256.casefold()
        except OSError:
            findings.append(_finding("source-missing-or-unreadable", (record,)))
            return tuple(findings)
    if changed:
        findings.append(_finding("source-fingerprint-changed", (record,)))
    return tuple(findings)


def _validate_destination_state(
    record: PreflightRecord,
    destination_root: AuthorizedDestinationRoot,
    *,
    max_path_length: int,
    max_component_length: int,
) -> tuple[PreflightFinding, ...]:
    destination = record.destination_relative_path
    if destination is None or not destination:
        return (_finding("matched-destination-missing", (record,)),)

    codes = _relative_path_codes(
        destination,
        prefix="destination",
        max_path_length=max_path_length,
        max_component_length=max_component_length,
    )
    findings = [_finding(code, (record,)) for code in codes]
    if codes:
        return tuple(findings)

    candidate = _candidate_path(destination_root.path, destination)
    if _path_traverses_link(destination_root.path, destination):
        findings.append(_finding("destination-link-or-junction-traversal", (record,)))
        return tuple(findings)
    if not _resolved_inside(destination_root.path, candidate):
        findings.append(_finding("destination-root-escape", (record,)))
        return tuple(findings)
    if candidate.exists():
        code = "destination-directory-exists" if candidate.is_dir() else "destination-file-exists"
        findings.append(_finding(code, (record,)))
    elif candidate.is_symlink():
        findings.append(_finding("destination-link-or-junction-traversal", (record,)))
    return tuple(findings)


def _collision_findings(records: tuple[PreflightRecord, ...]) -> tuple[PreflightFinding, ...]:
    matched = tuple(
        record
        for record in records
        if record.status is PreflightStatus.MATCHED and record.destination_relative_path
    )
    findings: list[PreflightFinding] = []

    exact: dict[str, list[PreflightRecord]] = defaultdict(list)
    by_nfc: dict[str, list[PreflightRecord]] = defaultdict(list)
    by_casefold: dict[str, list[PreflightRecord]] = defaultdict(list)
    for record in matched:
        destination = record.destination_relative_path
        assert destination is not None
        normalized = unicodedata.normalize("NFC", destination)
        exact[destination].append(record)
        by_nfc[normalized].append(record)
        by_casefold[normalized.casefold()].append(record)

    for candidates in exact.values():
        if len(candidates) > 1:
            findings.append(_finding("destination-exact-collision", candidates))

    for candidates in by_nfc.values():
        raw_paths = {record.destination_relative_path for record in candidates}
        if len(candidates) > 1 and len(raw_paths) > 1:
            findings.append(_finding("destination-unicode-normalization-collision", candidates))

    for candidates in by_casefold.values():
        normalized_paths = {
            unicodedata.normalize("NFC", record.destination_relative_path or "")
            for record in candidates
        }
        if len(candidates) > 1 and len(normalized_paths) > 1:
            findings.append(_finding("destination-case-insensitive-collision", candidates))

    return tuple(findings)


def _group_identity_findings(
    records: tuple[PreflightRecord, ...],
) -> tuple[PreflightFinding, ...]:
    groups: dict[str, list[PreflightRecord]] = defaultdict(list)
    for record in records:
        if record.operation_group_id:
            groups[record.operation_group_id].append(record)

    findings: list[PreflightFinding] = []
    for members in groups.values():
        matched = [member for member in members if member.status is PreflightStatus.MATCHED]
        providers = {member.provider_identity for member in matched if member.provider_identity}
        numbering = {member.numbering_identity for member in matched if member.numbering_identity}
        if len(providers) > 1:
            findings.append(_finding("operation-group-mixed-provider-identity", members))
        if len(numbering) > 1:
            findings.append(_finding("operation-group-mixed-numbering-identity", members))
    return tuple(findings)


def _duplicate_findings(
    records: tuple[PreflightRecord, ...],
) -> tuple[PreflightFinding, ...]:
    by_source = {record.source_relative_path: record for record in records}
    findings: list[PreflightFinding] = []
    for record in records:
        decision = record.duplicate
        if decision is None:
            continue
        if record.status is not PreflightStatus.MATCHED:
            continue
        if decision.winner is None:
            findings.append(_finding("unresolved-duplicate-group-marked-matched", (record,)))
            continue
        if decision.winner != record.source_relative_path:
            findings.append(_finding("duplicate-loser-marked-matched", (record,)))
            continue
        matched_losers = [
            by_source[loser]
            for loser in decision.losers
            if loser in by_source and by_source[loser].status is PreflightStatus.MATCHED
        ]
        if matched_losers:
            findings.append(
                _finding("duplicate-loser-also-marked-matched", (record, *matched_losers))
            )
    return tuple(findings)


def preflight_plan(
    plan_hash: str,
    records: Iterable[PreflightRecord],
    *,
    source_root: AuthorizedShowsRoot,
    destination_root: AuthorizedDestinationRoot,
    max_path_length: int = 240,
    max_component_length: int = 255,
) -> PreflightResult:
    """Validate a finalized operation set without mutating source or destination media."""

    if not _PLAN_HASH.fullmatch(plan_hash):
        raise ValueError("plan_hash must contain 64 hexadecimal characters")
    if not isinstance(source_root, AuthorizedShowsRoot):
        raise TypeError("preflight requires an explicitly authorized Shows root")
    if not isinstance(destination_root, AuthorizedDestinationRoot):
        raise TypeError("preflight requires an explicitly authorized destination root")
    if max_path_length < 80:
        raise ValueError("max_path_length must be at least 80")
    if not 32 <= max_component_length <= 255:
        raise ValueError("max_component_length must be between 32 and 255")

    record_group = tuple(
        sorted(records, key=lambda record: (record.record_id.casefold(), record.record_id))
    )
    findings: list[PreflightFinding] = []

    record_id_counts = Counter(record.record_id for record in record_group)
    for record_id, count in sorted(record_id_counts.items()):
        if count > 1:
            duplicates = [record for record in record_group if record.record_id == record_id]
            findings.append(_finding("duplicate-preflight-record-id", duplicates))

    source_counts = Counter(record.source_relative_path for record in record_group)
    for source_path, count in sorted(source_counts.items()):
        if count > 1:
            duplicates = [
                record for record in record_group if record.source_relative_path == source_path
            ]
            findings.append(_finding("duplicate-source-record", duplicates))

    for record in record_group:
        if record.status in {PreflightStatus.SUSPICIOUS, PreflightStatus.UNRESOLVED}:
            findings.append(
                _finding(f"blocking-plan-status:{record.status.value}", (record,))
            )
            continue
        if record.status is not PreflightStatus.MATCHED:
            continue

        if not record.operation_group_id:
            findings.append(_finding("matched-operation-group-missing", (record,)))
        if not record.provider_identity:
            findings.append(_finding("matched-provider-identity-missing", (record,)))
        if not record.numbering_identity:
            findings.append(_finding("matched-numbering-identity-missing", (record,)))

        findings.extend(_validate_source_state(record, source_root))
        findings.extend(
            _validate_destination_state(
                record,
                destination_root,
                max_path_length=max_path_length,
                max_component_length=max_component_length,
            )
        )

    findings.extend(_collision_findings(record_group))
    findings.extend(_group_identity_findings(record_group))
    findings.extend(_duplicate_findings(record_group))

    ordered = tuple(sorted(set(findings), key=_finding_sort_key))
    return PreflightResult(plan_hash=plan_hash.casefold(), findings=ordered)


def summarize_preflight(result: PreflightResult) -> str:
    if result.ready:
        return f"preflight ready for plan {result.plan_hash}: 0 blocking findings"
    counts = Counter(finding.code for finding in result.findings)
    breakdown = ", ".join(f"{code}={counts[code]}" for code in sorted(counts))
    return (
        f"preflight blocked for plan {result.plan_hash}: "
        f"{len(result.findings)} blocking findings ({breakdown})"
    )
