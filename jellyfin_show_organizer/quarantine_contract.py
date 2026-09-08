from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from .apply_contract import manifest_plan_hash
from .apply_execution import ApplyExecutionError, PreparedApply
from .models import SourceFingerprint

QUARANTINE_PLAN_SCHEMA_VERSION = 1


class QuarantineContractError(ValueError):
    """Raised when reviewed duplicate state cannot form a safe quarantine contract."""


class QuarantineMemberRole(StrEnum):
    VIDEO = "video"
    COMPANION = "companion"


def _path_key(value: str) -> str:
    return value.replace("\\", "/").casefold()


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise QuarantineContractError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise QuarantineContractError(f"{label} must be a non-empty string")
    return value


def _fingerprint(value: object, label: str) -> SourceFingerprint:
    raw = _mapping(value, label)
    size = raw.get("size")
    mtime_ns = raw.get("mtime_ns")
    sha256 = raw.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int):
        raise QuarantineContractError(f"{label}.size must be an integer")
    if isinstance(mtime_ns, bool) or not isinstance(mtime_ns, int):
        raise QuarantineContractError(f"{label}.mtime_ns must be an integer")
    if sha256 is not None and not isinstance(sha256, str):
        raise QuarantineContractError(f"{label}.sha256 must be a string or null")
    return SourceFingerprint(size=size, mtime_ns=mtime_ns, sha256=sha256)


def _fingerprint_payload(value: SourceFingerprint) -> dict[str, object]:
    return {
        "size": value.size,
        "mtime_ns": value.mtime_ns,
        "sha256": value.sha256,
    }


@dataclass(frozen=True, slots=True)
class QuarantineMember:
    role: QuarantineMemberRole
    source_relative_path: str
    fingerprint: SourceFingerprint

    def __post_init__(self) -> None:
        if not self.source_relative_path:
            raise ValueError("quarantine member source cannot be empty")


@dataclass(frozen=True, slots=True)
class QuarantineWinner:
    source_relative_path: str
    organized_relative_path: str
    fingerprint: SourceFingerprint

    def __post_init__(self) -> None:
        if not self.source_relative_path or not self.organized_relative_path:
            raise ValueError("quarantine winner paths cannot be empty")


@dataclass(frozen=True, slots=True)
class QuarantineGroup:
    group_id: str
    duplicate_destination_key: str
    winner: QuarantineWinner
    members: tuple[QuarantineMember, ...]

    def __post_init__(self) -> None:
        if not self.group_id:
            raise ValueError("quarantine group_id cannot be empty")
        if not self.duplicate_destination_key:
            raise ValueError("duplicate destination key cannot be empty")
        if not self.members:
            raise ValueError("quarantine groups require at least one member")
        if sum(member.role is QuarantineMemberRole.VIDEO for member in self.members) != 1:
            raise ValueError("quarantine groups require exactly one loser video")
        paths = [_path_key(member.source_relative_path) for member in self.members]
        if len(paths) != len(set(paths)):
            raise ValueError("quarantine group member paths must be unique")


@dataclass(frozen=True, slots=True)
class QuarantinePlan:
    schema_version: int
    plan_sha256: str
    review_session_sha256: str
    source_revision: str
    groups: tuple[QuarantineGroup, ...]

    def __post_init__(self) -> None:
        if self.schema_version != QUARANTINE_PLAN_SCHEMA_VERSION:
            raise ValueError("unsupported quarantine plan schema version")
        if len(self.plan_sha256) != 64 or len(self.review_session_sha256) != 64:
            raise ValueError("quarantine plan hashes must be SHA-256 digests")
        if len(self.source_revision) != 40:
            raise ValueError("quarantine source revision must be a Git commit SHA")
        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("quarantine group IDs must be unique")

    @property
    def canonical_bytes(self) -> bytes:
        payload = {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "review_session_sha256": self.review_session_sha256,
            "source_revision": self.source_revision,
            "groups": [
                {
                    "group_id": group.group_id,
                    "duplicate_destination_key": group.duplicate_destination_key,
                    "winner": {
                        "source_relative_path": group.winner.source_relative_path,
                        "organized_relative_path": group.winner.organized_relative_path,
                        "fingerprint": _fingerprint_payload(group.winner.fingerprint),
                    },
                    "members": [
                        {
                            "role": member.role.value,
                            "source_relative_path": member.source_relative_path,
                            "fingerprint": _fingerprint_payload(member.fingerprint),
                        }
                        for member in group.members
                    ],
                }
                for group in self.groups
            ],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


def _record_source(record: Mapping[str, object], index: int) -> tuple[str, SourceFingerprint]:
    source = _mapping(record.get("source"), f"records[{index}].source")
    return (
        _string(source.get("relative_path"), f"records[{index}].source.relative_path"),
        _fingerprint(source.get("fingerprint"), f"records[{index}].source.fingerprint"),
    )


def _group_id(plan_sha256: str, loser: str, winner: str, destination_key: str) -> str:
    payload = "\0".join(
        (
            plan_sha256,
            _path_key(loser),
            _path_key(winner),
            destination_key.casefold(),
        )
    ).encode("utf-8")
    return "quarantine-" + hashlib.sha256(payload).hexdigest()[:20]


def _duplicate_strings(
    duplicate: Mapping[str, object], index: int
) -> tuple[tuple[str, ...], tuple[str, ...], str, str]:
    raw_candidates = duplicate.get("candidates")
    raw_losers = duplicate.get("losers")
    if not isinstance(raw_candidates, list | tuple) or not all(
        isinstance(item, str) and item for item in raw_candidates
    ):
        raise QuarantineContractError(
            f"records[{index}].duplicate.candidates must contain source paths"
        )
    if not isinstance(raw_losers, list | tuple) or not all(
        isinstance(item, str) and item for item in raw_losers
    ):
        raise QuarantineContractError(
            f"records[{index}].duplicate.losers must contain source paths"
        )
    candidates = tuple(cast(tuple[str, ...], tuple(raw_candidates)))
    losers = tuple(cast(tuple[str, ...], tuple(raw_losers)))
    winner = _string(duplicate.get("winner"), f"records[{index}].duplicate.winner")
    destination_key = _string(
        duplicate.get("destination_key"),
        f"records[{index}].duplicate.destination_key",
    )
    candidate_keys = {_path_key(item) for item in candidates}
    loser_keys = {_path_key(item) for item in losers}
    winner_key = _path_key(winner)
    if len(candidate_keys) != len(candidates) or len(loser_keys) != len(losers):
        raise QuarantineContractError("duplicate candidate/loser paths must be unique")
    if winner_key not in candidate_keys or winner_key in loser_keys:
        raise QuarantineContractError("duplicate winner/candidate state is inconsistent")
    if loser_keys != candidate_keys - {winner_key}:
        raise QuarantineContractError(
            "duplicate loser set must equal every non-winner candidate"
        )
    return candidates, losers, winner, destination_key


def derive_quarantine_plan(
    manifest: object,
    prepared_apply: PreparedApply,
) -> QuarantinePlan:
    """Derive only explicit reviewed duplicate losers from one exact approved plan."""

    try:
        actual_plan_sha256 = manifest_plan_hash(manifest)
    except Exception as exc:
        raise QuarantineContractError("could not validate the reviewed plan") from exc
    if actual_plan_sha256 != prepared_apply.contract.plan_sha256:
        raise QuarantineContractError(
            "quarantine derivation plan does not match exact apply approval"
        )
    root = _mapping(manifest, "plan")
    raw_records = root.get("records")
    raw_companions = root.get("companions")
    if not isinstance(raw_records, list | tuple) or not isinstance(
        raw_companions, list | tuple
    ):
        raise QuarantineContractError("plan records/companions must be arrays")

    records: list[Mapping[str, object]] = []
    record_by_source: dict[str, tuple[int, Mapping[str, object]]] = {}
    for index, value in enumerate(raw_records):
        record = _mapping(value, f"records[{index}]")
        source_path, _ = _record_source(record, index)
        key = _path_key(source_path)
        if key in record_by_source:
            raise QuarantineContractError("plan repeats a video source path")
        record_by_source[key] = (index, record)
        records.append(record)

    duplicate_companions: dict[str, list[QuarantineMember]] = defaultdict(list)
    duplicate_companion_count = 0
    for index, value in enumerate(raw_companions):
        companion = _mapping(value, f"companions[{index}]")
        if companion.get("status") != "duplicate":
            continue
        duplicate_companion_count += 1
        if companion.get("destination") is not None:
            raise QuarantineContractError(
                "duplicate companion unexpectedly has a movement destination"
            )
        source_video = _string(
            companion.get("source_video"), f"companions[{index}].source_video"
        )
        duplicate_companions[_path_key(source_video)].append(
            QuarantineMember(
                role=QuarantineMemberRole.COMPANION,
                source_relative_path=_string(
                    companion.get("relative_path"),
                    f"companions[{index}].relative_path",
                ),
                fingerprint=_fingerprint(
                    companion.get("fingerprint"),
                    f"companions[{index}].fingerprint",
                ),
            )
        )

    groups: list[QuarantineGroup] = []
    consumed_companions = 0
    duplicate_video_count = 0
    for index, record in enumerate(records):
        if record.get("status") != "duplicate":
            continue
        duplicate_video_count += 1
        loser_path, loser_fingerprint = _record_source(record, index)
        duplicate = _mapping(record.get("duplicate"), f"records[{index}].duplicate")
        candidates, losers, winner_path, destination_key = _duplicate_strings(
            duplicate, index
        )
        del candidates
        if _path_key(loser_path) not in {_path_key(item) for item in losers}:
            raise QuarantineContractError(
                "duplicate-status record is not listed as a reviewed loser"
            )

        winner_entry = record_by_source.get(_path_key(winner_path))
        if winner_entry is None:
            raise QuarantineContractError("duplicate winner record is missing")
        winner_index, winner_record = winner_entry
        if winner_record.get("status") != "matched":
            raise QuarantineContractError(
                "duplicate winner must be an approved matched record"
            )
        winner_source, winner_fingerprint = _record_source(
            winner_record, winner_index
        )
        winner_destination = _string(
            winner_record.get("destination"),
            f"records[{winner_index}].destination",
        )
        loser_destination = _string(
            record.get("destination"), f"records[{index}].destination"
        )
        if _path_key(winner_destination) != _path_key(loser_destination):
            raise QuarantineContractError(
                "duplicate winner and loser do not share the reviewed destination"
            )
        winner_duplicate = _mapping(
            winner_record.get("duplicate"), f"records[{winner_index}].duplicate"
        )
        _, winner_losers, winner_selected, winner_destination_key = _duplicate_strings(
            winner_duplicate, winner_index
        )
        if (
            _path_key(winner_selected) != _path_key(winner_source)
            or {_path_key(item) for item in winner_losers}
            != {_path_key(item) for item in losers}
            or winner_destination_key.casefold() != destination_key.casefold()
        ):
            raise QuarantineContractError(
                "winner and loser records disagree about the reviewed duplicate decision"
            )

        companions = tuple(
            sorted(
                duplicate_companions.get(_path_key(loser_path), ()),
                key=lambda member: (
                    _path_key(member.source_relative_path),
                    member.source_relative_path,
                ),
            )
        )
        consumed_companions += len(companions)
        members = (
            QuarantineMember(
                role=QuarantineMemberRole.VIDEO,
                source_relative_path=loser_path,
                fingerprint=loser_fingerprint,
            ),
            *companions,
        )
        groups.append(
            QuarantineGroup(
                group_id=_group_id(
                    actual_plan_sha256,
                    loser_path,
                    winner_source,
                    destination_key,
                ),
                duplicate_destination_key=destination_key,
                winner=QuarantineWinner(
                    source_relative_path=winner_source,
                    organized_relative_path=winner_destination,
                    fingerprint=winner_fingerprint,
                ),
                members=members,
            )
        )

    if duplicate_video_count == 0:
        raise QuarantineContractError("reviewed plan contains no duplicate losers")
    if consumed_companions != duplicate_companion_count:
        raise QuarantineContractError(
            "duplicate companion exists without a duplicate loser video"
        )

    ordered = tuple(
        sorted(
            groups,
            key=lambda group: (
                _path_key(group.members[0].source_relative_path),
                group.group_id,
            ),
        )
    )
    member_paths = [
        _path_key(member.source_relative_path)
        for group in ordered
        for member in group.members
    ]
    if len(member_paths) != len(set(member_paths)):
        raise QuarantineContractError("quarantine plan repeats a member source path")
    return QuarantinePlan(
        schema_version=QUARANTINE_PLAN_SCHEMA_VERSION,
        plan_sha256=actual_plan_sha256,
        review_session_sha256=prepared_apply.review_session_sha256,
        source_revision=prepared_apply.source_revision,
        groups=ordered,
    )


def render_quarantine_plan(plan: QuarantinePlan) -> bytes:
    return plan.canonical_bytes + b"\n"


def load_quarantine_plan(payload: bytes) -> QuarantinePlan:
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise QuarantineContractError("invalid quarantine plan JSON") from exc
    root = _mapping(raw, "quarantine plan")
    expected = {
        "schema_version",
        "plan_sha256",
        "review_session_sha256",
        "source_revision",
        "groups",
    }
    if set(root) != expected:
        raise QuarantineContractError("quarantine plan has unexpected fields")
    raw_groups = root.get("groups")
    if not isinstance(raw_groups, list):
        raise QuarantineContractError("quarantine plan groups must be an array")
    groups: list[QuarantineGroup] = []
    for group_index, raw_group in enumerate(raw_groups):
        group = _mapping(raw_group, f"groups[{group_index}]")
        if set(group) != {
            "group_id",
            "duplicate_destination_key",
            "winner",
            "members",
        }:
            raise QuarantineContractError("quarantine group has unexpected fields")
        winner = _mapping(group.get("winner"), f"groups[{group_index}].winner")
        if set(winner) != {
            "source_relative_path",
            "organized_relative_path",
            "fingerprint",
        }:
            raise QuarantineContractError("quarantine winner has unexpected fields")
        raw_members = group.get("members")
        if not isinstance(raw_members, list):
            raise QuarantineContractError("quarantine members must be an array")
        members: list[QuarantineMember] = []
        for member_index, raw_member in enumerate(raw_members):
            member = _mapping(
                raw_member, f"groups[{group_index}].members[{member_index}]"
            )
            if set(member) != {"role", "source_relative_path", "fingerprint"}:
                raise QuarantineContractError(
                    "quarantine member has unexpected fields"
                )
            try:
                role = QuarantineMemberRole(
                    _string(member.get("role"), "quarantine member role")
                )
            except ValueError as exc:
                raise QuarantineContractError(
                    "quarantine member role is invalid"
                ) from exc
            members.append(
                QuarantineMember(
                    role=role,
                    source_relative_path=_string(
                        member.get("source_relative_path"),
                        "quarantine member source_relative_path",
                    ),
                    fingerprint=_fingerprint(
                        member.get("fingerprint"),
                        "quarantine member fingerprint",
                    ),
                )
            )
        groups.append(
            QuarantineGroup(
                group_id=_string(group.get("group_id"), "quarantine group_id"),
                duplicate_destination_key=_string(
                    group.get("duplicate_destination_key"),
                    "quarantine duplicate_destination_key",
                ),
                winner=QuarantineWinner(
                    source_relative_path=_string(
                        winner.get("source_relative_path"),
                        "quarantine winner source_relative_path",
                    ),
                    organized_relative_path=_string(
                        winner.get("organized_relative_path"),
                        "quarantine winner organized_relative_path",
                    ),
                    fingerprint=_fingerprint(
                        winner.get("fingerprint"), "quarantine winner fingerprint"
                    ),
                ),
                members=tuple(members),
            )
        )
    try:
        return QuarantinePlan(
            schema_version=cast(int, root.get("schema_version")),
            plan_sha256=_string(root.get("plan_sha256"), "quarantine plan_sha256"),
            review_session_sha256=_string(
                root.get("review_session_sha256"),
                "quarantine review_session_sha256",
            ),
            source_revision=_string(
                root.get("source_revision"), "quarantine source_revision"
            ),
            groups=tuple(groups),
        )
    except (TypeError, ValueError) as exc:
        raise QuarantineContractError(str(exc)) from exc


def validate_quarantine_plan_binding(
    supplied: QuarantinePlan,
    manifest: object,
    prepared_apply: PreparedApply,
) -> QuarantinePlan:
    expected = derive_quarantine_plan(manifest, prepared_apply)
    if supplied.canonical_bytes != expected.canonical_bytes:
        raise QuarantineContractError(
            "quarantine plan does not match the exact reviewed duplicate outcome"
        )
    return expected


def quarantine_plan_from_paths(
    plan_path: str,
    prepared_apply: PreparedApply,
) -> QuarantinePlan:
    try:
        manifest = json.loads(open(plan_path, encoding="utf-8").read())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApplyExecutionError("could not load plan for quarantine derivation") from exc
    return derive_quarantine_plan(manifest, prepared_apply)
