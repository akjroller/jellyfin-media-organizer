from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from .apply_contract import ApplyContract
from .apply_execution import PreparedApply
from .review_session import atomic_write_new

APPLY_SCOPE_SCHEMA_VERSION = 1
_HASH = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class ApplyScopeError(ValueError):
    """Raised when an immutable apply scope is invalid or stale."""


def _group_sort_key(value: str) -> tuple[str, str]:
    return value.casefold(), value


@dataclass(frozen=True, slots=True)
class ApplyScope:
    schema_version: int
    plan_sha256: str
    review_session_sha256: str
    source_revision: str
    group_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != APPLY_SCOPE_SCHEMA_VERSION:
            raise ValueError("unsupported apply scope schema version")
        if _HASH.fullmatch(self.plan_sha256) is None:
            raise ValueError("apply scope plan_sha256 must be a SHA-256 digest")
        if _HASH.fullmatch(self.review_session_sha256) is None:
            raise ValueError(
                "apply scope review_session_sha256 must be a SHA-256 digest"
            )
        if _REVISION.fullmatch(self.source_revision) is None:
            raise ValueError("apply scope source_revision must be a Git commit SHA")
        if not self.group_ids:
            raise ValueError("apply scope requires at least one operation group")
        if any(not value for value in self.group_ids):
            raise ValueError("apply scope group IDs cannot be empty")
        ordered = tuple(sorted(self.group_ids, key=_group_sort_key))
        if ordered != self.group_ids:
            raise ValueError("apply scope group IDs must be sorted")
        if len(self.group_ids) != len(set(self.group_ids)):
            raise ValueError("apply scope group IDs must be unique")

    @property
    def canonical_bytes(self) -> bytes:
        payload = {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "review_session_sha256": self.review_session_sha256,
            "source_revision": self.source_revision,
            "group_ids": list(self.group_ids),
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


def create_apply_scope(
    prepared: PreparedApply,
    group_ids: tuple[str, ...],
) -> ApplyScope:
    """Create a deliberate non-empty proper subset of one complete apply contract."""

    if prepared.apply_scope_sha256 is not None:
        raise ApplyScopeError(
            "cannot create an apply scope from an already scoped apply"
        )
    if not group_ids:
        raise ApplyScopeError("apply scope creation requires explicit operation groups")
    if any(not value for value in group_ids):
        raise ApplyScopeError("apply scope group IDs cannot be empty")
    if len(group_ids) != len(set(group_ids)):
        raise ApplyScopeError("apply scope group IDs must be unique")

    available = {group.group_id for group in prepared.contract.groups}
    unknown = sorted(set(group_ids) - available, key=_group_sort_key)
    if unknown:
        raise ApplyScopeError(
            "apply scope contains unknown operation groups: " + ", ".join(unknown)
        )
    if len(group_ids) >= len(prepared.contract.groups):
        raise ApplyScopeError(
            "canary apply scope must be a proper subset; use unscoped apply for the full plan"
        )

    return ApplyScope(
        schema_version=APPLY_SCOPE_SCHEMA_VERSION,
        plan_sha256=prepared.contract.plan_sha256,
        review_session_sha256=prepared.review_session_sha256,
        source_revision=prepared.source_revision,
        group_ids=tuple(sorted(group_ids, key=_group_sort_key)),
    )


def render_apply_scope(scope: ApplyScope) -> bytes:
    return scope.canonical_bytes + b"\n"


def load_apply_scope(payload: bytes) -> ApplyScope:
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ApplyScopeError("invalid apply scope JSON") from exc
    if not isinstance(raw, dict):
        raise ApplyScopeError("apply scope must be an object")
    expected = {
        "schema_version",
        "plan_sha256",
        "review_session_sha256",
        "source_revision",
        "group_ids",
    }
    if set(raw) != expected:
        raise ApplyScopeError("apply scope has unexpected fields")
    schema_version = raw.get("schema_version")
    plan_sha256 = raw.get("plan_sha256")
    review_session_sha256 = raw.get("review_session_sha256")
    source_revision = raw.get("source_revision")
    group_ids = raw.get("group_ids")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ApplyScopeError("apply scope schema_version must be an integer")
    if not isinstance(plan_sha256, str):
        raise ApplyScopeError("apply scope plan_sha256 must be a string")
    if not isinstance(review_session_sha256, str):
        raise ApplyScopeError("apply scope review_session_sha256 must be a string")
    if not isinstance(source_revision, str):
        raise ApplyScopeError("apply scope source_revision must be a string")
    if not isinstance(group_ids, list) or not all(
        isinstance(value, str) for value in group_ids
    ):
        raise ApplyScopeError("apply scope group_ids must be strings")
    try:
        return ApplyScope(
            schema_version=schema_version,
            plan_sha256=plan_sha256,
            review_session_sha256=review_session_sha256,
            source_revision=source_revision,
            group_ids=tuple(cast(list[str], group_ids)),
        )
    except (TypeError, ValueError) as exc:
        raise ApplyScopeError(str(exc)) from exc


def prepare_scoped_apply(
    prepared: PreparedApply,
    payload: bytes,
    *,
    approved_scope_sha256: str,
) -> PreparedApply:
    """Bind one exact canonical scope artifact to a fully reviewed apply contract."""

    if _HASH.fullmatch(approved_scope_sha256) is None:
        raise ApplyScopeError("approved apply-scope SHA-256 has an invalid format")
    if prepared.apply_scope_sha256 is not None:
        raise ApplyScopeError("apply is already bound to a scope")
    scope = load_apply_scope(payload)
    if scope.sha256 != approved_scope_sha256:
        raise ApplyScopeError(
            "approved apply-scope SHA-256 does not match the supplied artifact"
        )
    if scope.plan_sha256 != prepared.contract.plan_sha256:
        raise ApplyScopeError("apply scope belongs to another plan")
    if scope.review_session_sha256 != prepared.review_session_sha256:
        raise ApplyScopeError("apply scope belongs to another review session")
    if scope.source_revision != prepared.source_revision:
        raise ApplyScopeError("apply scope belongs to another source revision")

    available = {group.group_id: group for group in prepared.contract.groups}
    unknown = [group_id for group_id in scope.group_ids if group_id not in available]
    if unknown:
        raise ApplyScopeError("apply scope references an unavailable operation group")
    if len(scope.group_ids) >= len(prepared.contract.groups):
        raise ApplyScopeError(
            "canary apply scope must remain a proper subset of the approved plan"
        )
    selected = tuple(
        group for group in prepared.contract.groups if group.group_id in scope.group_ids
    )
    if len(selected) != len(scope.group_ids):
        raise ApplyScopeError("apply scope operation-group set is inconsistent")

    contract = ApplyContract(
        plan_sha256=prepared.contract.plan_sha256,
        groups=selected,
        separate_roots=prepared.contract.separate_roots,
    )
    return replace(
        prepared,
        contract=contract,
        apply_scope_sha256=scope.sha256,
    )


def write_apply_scope(path: Path, scope: ApplyScope) -> None:
    """Publish a new immutable scope artifact without replacing an existing path."""

    try:
        atomic_write_new(path, render_apply_scope(scope))
    except FileExistsError as exc:
        raise ApplyScopeError("apply scope output already exists") from exc
