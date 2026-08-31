from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from .models import CacheSnapshot, SourceFingerprint
from .schema import PLAN_SCHEMA_VERSION, validate_manifest

_HASH = re.compile(r"^[0-9a-f]{64}$")
_PREFLIGHT_SCHEMA_VERSION = 1


class ApplyContractError(ValueError):
    """Raised when an approved plan cannot safely cross the apply boundary."""


class ApplyMemberRole(StrEnum):
    VIDEO = "video"
    COMPANION = "companion"


class JournalEvent(StrEnum):
    GROUP_STARTED = "group-started"
    MEMBER_COMPLETED = "member-completed"
    GROUP_COMPLETED = "group-completed"
    GROUP_FAILED = "group-failed"


@dataclass(frozen=True, slots=True)
class ApplyApproval:
    """Explicit human approval context for one exact immutable plan."""

    plan_sha256: str
    schema_version: int
    tool_version: str
    config_snapshot_id: str
    overrides_snapshot_id: str
    cache_snapshots: tuple[CacheSnapshot, ...]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("plan_sha256", self.plan_sha256),
            ("config_snapshot_id", self.config_snapshot_id),
            ("overrides_snapshot_id", self.overrides_snapshot_id),
        ):
            _validate_hash(value, field_name)
        if self.schema_version <= 0:
            raise ValueError("approval schema_version must be positive")
        if not self.tool_version:
            raise ValueError("approval tool_version cannot be empty")
        ordered = tuple(sorted(self.cache_snapshots, key=_cache_snapshot_key))
        if len(ordered) != len({_cache_snapshot_key(item) for item in ordered}):
            raise ValueError("approval cache snapshots must be unique")
        object.__setattr__(self, "cache_snapshots", ordered)


@dataclass(frozen=True, slots=True)
class ApplyMember:
    role: ApplyMemberRole
    source_relative_path: str
    destination_relative_path: str
    fingerprint: SourceFingerprint

    def __post_init__(self) -> None:
        if not self.source_relative_path:
            raise ValueError("apply member source cannot be empty")
        if not self.destination_relative_path:
            raise ValueError("apply member destination cannot be empty")

    @property
    def moving(self) -> bool:
        return _path_key(self.source_relative_path) != _path_key(
            self.destination_relative_path
        )


@dataclass(frozen=True, slots=True)
class ApplyOperationGroup:
    group_id: str
    members: tuple[ApplyMember, ...]

    def __post_init__(self) -> None:
        if not self.group_id:
            raise ValueError("apply group_id cannot be empty")
        if not self.members:
            raise ValueError("apply groups require at least one member")
        video_count = sum(member.role is ApplyMemberRole.VIDEO for member in self.members)
        if video_count != 1:
            raise ValueError("apply groups require exactly one video member")
        sources = [_path_key(member.source_relative_path) for member in self.members]
        destinations = [
            _path_key(member.destination_relative_path) for member in self.members
        ]
        if len(sources) != len(set(sources)):
            raise ValueError("apply group sources must be unique")
        if len(destinations) != len(set(destinations)):
            raise ValueError("apply group destinations must be unique")

    @property
    def moving_members(self) -> tuple[ApplyMember, ...]:
        return tuple(member for member in self.members if member.moving)


@dataclass(frozen=True, slots=True)
class ApplyContract:
    plan_sha256: str
    groups: tuple[ApplyOperationGroup, ...]

    def __post_init__(self) -> None:
        _validate_hash(self.plan_sha256, "plan_sha256")
        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("apply group ids must be unique")


@dataclass(frozen=True, slots=True)
class ApplyJournalEntry:
    sequence: int
    plan_sha256: str
    group_id: str
    event: JournalEvent
    source_relative_path: str | None = None
    destination_relative_path: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("journal sequence must be positive")
        _validate_hash(self.plan_sha256, "journal plan_sha256")
        if not self.group_id:
            raise ValueError("journal group_id cannot be empty")
        has_source = self.source_relative_path is not None
        has_destination = self.destination_relative_path is not None
        if has_source != has_destination:
            raise ValueError(
                "journal member source and destination must be supplied together"
            )
        if self.event is JournalEvent.MEMBER_COMPLETED and not has_source:
            raise ValueError("member-completed journal entries require member paths")
        if self.event is not JournalEvent.MEMBER_COMPLETED and has_source:
            raise ValueError("only member-completed entries may carry member paths")
        if self.detail is not None and not self.detail.strip():
            raise ValueError("journal detail cannot be blank")


@dataclass(frozen=True, slots=True)
class JournalReplay:
    completed_group_ids: tuple[str, ...]
    incomplete_group_ids: tuple[str, ...]
    completed_members: tuple[tuple[str, str], ...]


def _validate_hash(value: str, field: str) -> None:
    if _HASH.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _path_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.replace("\\", "/"))
    return normalized.casefold()


def _cache_snapshot_key(snapshot: CacheSnapshot) -> tuple[str, str, str, str, str]:
    return (
        snapshot.provider,
        snapshot.kind,
        snapshot.request_key,
        snapshot.snapshot_id,
        snapshot.state,
    )


def manifest_plan_hash(manifest: object) -> str:
    """Validate and hash one serialized plan using the canonical plan encoding."""

    validate_manifest(manifest)
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ApplyContractError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ApplyContractError(f"{field} must be a non-empty string")
    return value


def _fingerprint(value: object, field: str) -> SourceFingerprint:
    raw = _mapping(value, field)
    size = raw.get("size")
    mtime_ns = raw.get("mtime_ns")
    sha256 = raw.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int):
        raise ApplyContractError(f"{field}.size must be an integer")
    if isinstance(mtime_ns, bool) or not isinstance(mtime_ns, int):
        raise ApplyContractError(f"{field}.mtime_ns must be an integer")
    if sha256 is not None and not isinstance(sha256, str):
        raise ApplyContractError(f"{field}.sha256 must be a string or null")
    return SourceFingerprint(size=size, mtime_ns=mtime_ns, sha256=sha256)


def _manifest_approval_context(
    manifest: Mapping[str, object],
) -> tuple[int, str, str, str, tuple[CacheSnapshot, ...]]:
    schema_version = manifest.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ApplyContractError("plan schema_version must be an integer")
    provenance = _mapping(manifest.get("provenance"), "provenance")
    tool_version = _string(provenance.get("tool_version"), "provenance.tool_version")
    config_snapshot_id = _string(
        provenance.get("config_snapshot_id"), "provenance.config_snapshot_id"
    )
    overrides_snapshot_id = _string(
        provenance.get("overrides_snapshot_id"), "provenance.overrides_snapshot_id"
    )
    raw_snapshots = provenance.get("cache_snapshots")
    if not isinstance(raw_snapshots, list | tuple):
        raise ApplyContractError("provenance.cache_snapshots must be an array")
    snapshots: list[CacheSnapshot] = []
    for index, value in enumerate(raw_snapshots):
        raw = _mapping(value, f"provenance.cache_snapshots[{index}]")
        snapshots.append(
            CacheSnapshot(
                provider=_string(raw.get("provider"), "cache snapshot provider"),
                kind=_string(raw.get("kind"), "cache snapshot kind"),
                request_key=_string(
                    raw.get("request_key"), "cache snapshot request_key"
                ),
                snapshot_id=_string(
                    raw.get("snapshot_id"), "cache snapshot snapshot_id"
                ),
                state=_string(raw.get("state"), "cache snapshot state"),
            )
        )
    return (
        schema_version,
        tool_version,
        config_snapshot_id,
        overrides_snapshot_id,
        tuple(sorted(snapshots, key=_cache_snapshot_key)),
    )


def _validate_approval(manifest: Mapping[str, object], approval: ApplyApproval) -> str:
    plan_hash = manifest_plan_hash(manifest)
    if approval.plan_sha256 != plan_hash:
        raise ApplyContractError("approved plan hash does not match the plan manifest")
    (
        schema_version,
        tool_version,
        config_snapshot_id,
        overrides_snapshot_id,
        cache_snapshots,
    ) = _manifest_approval_context(manifest)
    if approval.schema_version != schema_version or schema_version != PLAN_SCHEMA_VERSION:
        raise ApplyContractError("approved plan schema context does not match")
    if approval.tool_version != tool_version:
        raise ApplyContractError("approved tool version does not match")
    if approval.config_snapshot_id != config_snapshot_id:
        raise ApplyContractError("approved config snapshot does not match")
    if approval.overrides_snapshot_id != overrides_snapshot_id:
        raise ApplyContractError("approved override snapshot does not match")
    if approval.cache_snapshots != cache_snapshots:
        raise ApplyContractError("approved cache snapshot context does not match")
    return plan_hash


def _validate_preflight(preflight: object, plan_hash: str) -> None:
    raw = _mapping(preflight, "preflight")
    expected = {
        "schema_version",
        "plan_hash",
        "ready",
        "blocked_group_ids",
        "findings",
    }
    if set(raw) != expected:
        raise ApplyContractError("preflight has unexpected fields")
    if raw["schema_version"] != _PREFLIGHT_SCHEMA_VERSION:
        raise ApplyContractError("unsupported preflight schema_version")
    if raw["plan_hash"] != plan_hash:
        raise ApplyContractError("preflight plan hash does not match the approved plan")
    if raw["ready"] is not True:
        raise ApplyContractError("preflight is not ready")
    blocked = raw["blocked_group_ids"]
    findings = raw["findings"]
    if not isinstance(blocked, list | tuple) or blocked:
        raise ApplyContractError("preflight contains blocked operation groups")
    if not isinstance(findings, list | tuple) or findings:
        raise ApplyContractError("preflight contains blocking findings")


def _video_member(record: Mapping[str, object], index: int) -> ApplyMember | None:
    status = _string(record.get("status"), f"records[{index}].status")
    if status in {"suspicious", "unresolved"}:
        raise ApplyContractError("plan contains unresolved or suspicious video records")
    if status == "duplicate":
        return None
    if status not in {"matched", "extra"}:
        raise ApplyContractError("plan contains unsupported video status")
    source = _mapping(record.get("source"), f"records[{index}].source")
    source_path = _string(
        source.get("relative_path"), f"records[{index}].source.relative_path"
    )
    destination = _string(
        record.get("destination"), f"records[{index}].destination"
    )
    return ApplyMember(
        role=ApplyMemberRole.VIDEO,
        source_relative_path=source_path,
        destination_relative_path=destination,
        fingerprint=_fingerprint(
            source.get("fingerprint"), f"records[{index}].source.fingerprint"
        ),
    )


def _companion_member(
    companion: Mapping[str, object], index: int
) -> ApplyMember | None:
    status = _string(companion.get("status"), f"companions[{index}].status")
    if status == "unresolved":
        raise ApplyContractError("plan contains unresolved companion records")
    if status in {"duplicate", "ignored"}:
        return None
    if status != "associated":
        raise ApplyContractError("plan contains unsupported companion status")
    return ApplyMember(
        role=ApplyMemberRole.COMPANION,
        source_relative_path=_string(
            companion.get("relative_path"), f"companions[{index}].relative_path"
        ),
        destination_relative_path=_string(
            companion.get("destination"), f"companions[{index}].destination"
        ),
        fingerprint=_fingerprint(
            companion.get("fingerprint"), f"companions[{index}].fingerprint"
        ),
    )


def build_apply_contract(
    manifest: object,
    preflight: object,
    approval: ApplyApproval,
) -> ApplyContract:
    """Validate an approved immutable plan and derive non-mutating operation groups.

    This function deliberately performs no filesystem access and no media mutation.
    A future apply executor must consume this contract rather than rerunning matching
    or inventing destinations.
    """

    validate_manifest(manifest)
    root = cast(Mapping[str, object], manifest)
    plan_hash = _validate_approval(root, approval)
    _validate_preflight(preflight, plan_hash)

    raw_records = root["records"]
    raw_companions = root["companions"]
    assert isinstance(raw_records, list | tuple)
    assert isinstance(raw_companions, list | tuple)

    groups: dict[str, list[ApplyMember]] = defaultdict(list)
    video_group_by_source: dict[str, str] = {}
    for index, value in enumerate(raw_records):
        record = cast(Mapping[str, object], value)
        member = _video_member(record, index)
        if member is None:
            continue
        group_id = _string(
            record.get("operation_group_id"), f"records[{index}].operation_group_id"
        )
        source_key = _path_key(member.source_relative_path)
        if source_key in video_group_by_source:
            raise ApplyContractError("plan repeats a movable video source")
        video_group_by_source[source_key] = group_id
        groups[group_id].append(member)

    for index, value in enumerate(raw_companions):
        companion = cast(Mapping[str, object], value)
        member = _companion_member(companion, index)
        if member is None:
            continue
        source_video = _string(
            companion.get("source_video"), f"companions[{index}].source_video"
        )
        group_id = _string(
            companion.get("operation_group_id"),
            f"companions[{index}].operation_group_id",
        )
        expected_group = video_group_by_source.get(_path_key(source_video))
        if expected_group is None:
            raise ApplyContractError(
                "associated companion references a non-movable video operation"
            )
        if expected_group != group_id:
            raise ApplyContractError(
                "associated companion operation group does not match its video"
            )
        groups[group_id].append(member)

    operation_groups: list[ApplyOperationGroup] = []
    for group_id in sorted(groups, key=lambda value: (value.casefold(), value)):
        members = tuple(
            sorted(
                groups[group_id],
                key=lambda member: (
                    0 if member.role is ApplyMemberRole.VIDEO else 1,
                    _path_key(member.source_relative_path),
                    member.source_relative_path,
                ),
            )
        )
        group = ApplyOperationGroup(group_id=group_id, members=members)
        if group.moving_members:
            operation_groups.append(group)

    return ApplyContract(plan_sha256=plan_hash, groups=tuple(operation_groups))


def replay_journal(
    contract: ApplyContract,
    entries: Iterable[ApplyJournalEntry],
) -> JournalReplay:
    """Replay append-only journal events without touching the filesystem."""

    groups = {group.group_id: group for group in contract.groups}
    active: set[str] = set()
    completed_groups: set[str] = set()
    touched_groups: set[str] = set()
    completed_members: set[tuple[str, str]] = set()

    ordered = tuple(entries)
    expected_sequence = 1
    for entry in ordered:
        if entry.sequence != expected_sequence:
            raise ApplyContractError("journal sequence is not contiguous")
        expected_sequence += 1
        if entry.plan_sha256 != contract.plan_sha256:
            raise ApplyContractError("journal entry belongs to a different plan")
        group = groups.get(entry.group_id)
        if group is None:
            raise ApplyContractError("journal entry references an unknown group")
        if entry.group_id in completed_groups:
            raise ApplyContractError("journal contains events after group completion")
        touched_groups.add(entry.group_id)

        if entry.event is JournalEvent.GROUP_STARTED:
            if entry.group_id in active:
                raise ApplyContractError("journal starts an already active group")
            active.add(entry.group_id)
            continue

        if entry.event is JournalEvent.MEMBER_COMPLETED:
            if entry.group_id not in active:
                raise ApplyContractError(
                    "journal completes a member outside an active group"
                )
            assert entry.source_relative_path is not None
            assert entry.destination_relative_path is not None
            member_key = (
                _path_key(entry.source_relative_path),
                _path_key(entry.destination_relative_path),
            )
            allowed = {
                (
                    _path_key(member.source_relative_path),
                    _path_key(member.destination_relative_path),
                )
                for member in group.moving_members
            }
            if member_key not in allowed:
                raise ApplyContractError(
                    "journal member is not part of the approved group"
                )
            if member_key in completed_members:
                raise ApplyContractError("journal repeats a completed member move")
            completed_members.add(member_key)
            continue

        if entry.event is JournalEvent.GROUP_FAILED:
            if entry.group_id not in active:
                raise ApplyContractError("journal fails a group that is not active")
            active.remove(entry.group_id)
            continue

        if entry.event is JournalEvent.GROUP_COMPLETED:
            if entry.group_id not in active:
                raise ApplyContractError("journal completes a group that is not active")
            required = {
                (
                    _path_key(member.source_relative_path),
                    _path_key(member.destination_relative_path),
                )
                for member in group.moving_members
            }
            if not required.issubset(completed_members):
                raise ApplyContractError(
                    "journal completes a group before all member moves are recorded"
                )
            active.remove(entry.group_id)
            completed_groups.add(entry.group_id)
            continue

        raise ApplyContractError("journal contains an unsupported event")

    incomplete = touched_groups - completed_groups
    return JournalReplay(
        completed_group_ids=tuple(
            sorted(completed_groups, key=lambda value: (value.casefold(), value))
        ),
        incomplete_group_ids=tuple(
            sorted(incomplete, key=lambda value: (value.casefold(), value))
        ),
        completed_members=tuple(sorted(completed_members)),
    )
