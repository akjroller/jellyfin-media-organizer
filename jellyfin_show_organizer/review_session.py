from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from .review_identity import (
    ReviewCandidateBinding,
    ReviewFingerprint,
    ReviewMemberBinding,
    duplicate_candidate_set_hash,
    normalize_review_path,
    stable_duplicate_ref,
)
from .schema import validate_manifest

REVIEW_SESSION_SCHEMA_VERSION = 1


class ReviewItemKind(StrEnum):
    DUPLICATE = "duplicate"
    HELD = "held"


class ReviewItemState(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    DEFERRED = "deferred"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def manifest_sha256(manifest: object) -> str:
    validate_manifest(manifest)
    return _sha256_bytes(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def stable_held_ref(source: str) -> str:
    digest = hashlib.sha256(normalize_review_path(source).encode("utf-8")).hexdigest()[:16]
    return f"held-{digest}"


@dataclass(frozen=True, slots=True)
class ReviewSessionItem:
    review_ref: str
    kind: ReviewItemKind
    state: ReviewItemState
    show_key: str
    source: str | None = None
    duplicate_ref: str | None = None
    candidate_set_sha256: str | None = None
    candidates: tuple[str, ...] = ()
    action: str | None = None
    data_json: str = "{}"

    def __post_init__(self) -> None:
        if not self.review_ref or not self.show_key:
            raise ValueError("review session items require review_ref and show_key")
        if self.kind is ReviewItemKind.HELD:
            if self.source is None or self.duplicate_ref is not None or self.candidates:
                raise ValueError("held review item has invalid identity fields")
        else:
            if (
                self.source is not None
                or self.duplicate_ref is None
                or self.candidate_set_sha256 is None
                or len(self.candidates) < 2
            ):
                raise ValueError("duplicate review item has invalid identity fields")
        try:
            data = json.loads(self.data_json)
        except json.JSONDecodeError as exc:
            raise ValueError("review item data_json must contain valid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("review item data_json must contain a JSON object")
        canonical = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(self, "data_json", canonical)
        if self.state is ReviewItemState.PENDING and self.action is not None:
            raise ValueError("pending review items cannot carry an action")
        if self.state is ReviewItemState.DEFERRED and self.action != "defer":
            raise ValueError("deferred review items must carry action='defer'")

    @property
    def data(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.data_json))


@dataclass(frozen=True, slots=True)
class ReviewSession:
    schema_version: int
    plan_sha256: str
    base_override_snapshot: str
    items: tuple[ReviewSessionItem, ...]

    def __post_init__(self) -> None:
        if self.schema_version != REVIEW_SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported review session schema version")
        for label, value in (
            ("plan_sha256", self.plan_sha256),
            ("base_override_snapshot", self.base_override_snapshot),
        ):
            if len(value) != 64:
                raise ValueError(f"{label} must contain 64 hex characters")
            try:
                int(value, 16)
            except ValueError as exc:
                raise ValueError(f"{label} must contain 64 hex characters") from exc
        refs = [item.review_ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("review session refs must be unique")

    @property
    def canonical_bytes(self) -> bytes:
        payload = {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "base_override_snapshot": self.base_override_snapshot,
            "items": [
                {
                    "action": item.action,
                    "candidate_set_sha256": item.candidate_set_sha256,
                    "candidates": list(item.candidates),
                    "data": item.data,
                    "duplicate_ref": item.duplicate_ref,
                    "kind": item.kind.value,
                    "review_ref": item.review_ref,
                    "show_key": item.show_key,
                    "source": item.source,
                    "state": item.state.value,
                }
                for item in sorted(self.items, key=lambda entry: entry.review_ref)
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
        return _sha256_bytes(self.canonical_bytes)

    def item(self, review_ref: str) -> ReviewSessionItem:
        match = next((item for item in self.items if item.review_ref == review_ref), None)
        if match is None:
            raise ValueError("review session does not contain requested review ref")
        return match

    def with_answer(
        self,
        review_ref: str,
        *,
        state: ReviewItemState,
        action: str,
        data: Mapping[str, object] | None = None,
    ) -> ReviewSession:
        if state is ReviewItemState.PENDING:
            raise ValueError("answers cannot transition an item back to pending")
        encoded = json.dumps(
            dict(data or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        updated = tuple(
            replace(item, state=state, action=action, data_json=encoded)
            if item.review_ref == review_ref
            else item
            for item in self.items
        )
        if updated == self.items:
            raise ValueError("review ref was not found")
        return replace(self, items=updated)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _fingerprint(value: object, label: str) -> ReviewFingerprint:
    raw = _mapping(value, label)
    size = raw.get("size")
    mtime_ns = raw.get("mtime_ns")
    sha256 = raw.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int):
        raise ValueError(f"{label}.size must be an integer")
    if isinstance(mtime_ns, bool) or not isinstance(mtime_ns, int):
        raise ValueError(f"{label}.mtime_ns must be an integer")
    if sha256 is not None and not isinstance(sha256, str):
        raise ValueError(f"{label}.sha256 must be a string")
    return ReviewFingerprint(size=size, mtime_ns=mtime_ns, sha256=sha256)


def _source_path(record: Mapping[str, object]) -> str:
    source = _mapping(record.get("source"), "record.source")
    path = source.get("relative_path")
    if not isinstance(path, str) or not path:
        raise ValueError("record source relative_path is invalid")
    return path


def _show_key(record: Mapping[str, object]) -> str:
    show = record.get("show")
    if isinstance(show, Mapping):
        source_key = show.get("source_key")
        if isinstance(source_key, str) and source_key:
            return source_key
    parse = record.get("parse")
    if isinstance(parse, Mapping):
        series_hint = parse.get("series_hint")
        if isinstance(series_hint, str) and series_hint:
            return series_hint
    source = _source_path(record).replace("\\", "/")
    return source.split("/", 1)[0]


def _candidate_binding(
    source: str,
    record_by_source: Mapping[str, Mapping[str, object]],
    companions_by_source: Mapping[str, tuple[Mapping[str, object], ...]],
) -> ReviewCandidateBinding:
    record = record_by_source.get(normalize_review_path(source))
    if record is None:
        raise ValueError("duplicate candidate is missing its plan record")
    source_raw = _mapping(record.get("source"), "record.source")
    source_member = ReviewMemberBinding(
        path=source,
        fingerprint=_fingerprint(source_raw.get("fingerprint"), "record.source.fingerprint"),
    )
    companion_members = []
    for companion in companions_by_source.get(normalize_review_path(source), ()):
        path = companion.get("relative_path")
        if not isinstance(path, str) or not path:
            raise ValueError("companion relative_path is invalid")
        fingerprint = companion.get("fingerprint")
        if fingerprint is None:
            continue
        companion_members.append(
            ReviewMemberBinding(
                path=path,
                fingerprint=_fingerprint(fingerprint, "companion.fingerprint"),
            )
        )
    return ReviewCandidateBinding(
        source=source_member,
        companions=tuple(
            sorted(
                companion_members,
                key=lambda member: (normalize_review_path(member.path), member.path),
            )
        ),
    )


def build_review_session(
    manifest: object,
    *,
    base_override_snapshot: str,
) -> ReviewSession:
    validate_manifest(manifest)
    root = cast(Mapping[str, object], manifest)
    raw_records = root.get("records")
    raw_companions = root.get("companions")
    assert isinstance(raw_records, list | tuple)
    assert isinstance(raw_companions, list | tuple)
    records = tuple(cast(Mapping[str, object], record) for record in raw_records)
    companions = tuple(cast(Mapping[str, object], item) for item in raw_companions)
    record_by_source = {
        normalize_review_path(_source_path(record)): record for record in records
    }
    companions_by_source: dict[str, list[Mapping[str, object]]] = {}
    for companion in companions:
        source_video = companion.get("source_video")
        if isinstance(source_video, str) and source_video:
            companions_by_source.setdefault(normalize_review_path(source_video), []).append(
                companion
            )
    frozen_companions = {
        key: tuple(value) for key, value in companions_by_source.items()
    }

    items: list[ReviewSessionItem] = []
    seen_duplicate_refs: set[str] = set()
    for record in records:
        duplicate = record.get("duplicate")
        if not isinstance(duplicate, Mapping):
            continue
        destination_key = duplicate.get("destination_key")
        raw_candidates = duplicate.get("candidates")
        if not isinstance(destination_key, str) or not isinstance(
            raw_candidates, list | tuple
        ):
            raise ValueError("duplicate decision is incomplete")
        candidates = tuple(
            candidate for candidate in raw_candidates if isinstance(candidate, str)
        )
        if len(candidates) != len(raw_candidates):
            raise ValueError("duplicate decision candidates are invalid")
        duplicate_ref = stable_duplicate_ref(destination_key, candidates)
        if duplicate_ref in seen_duplicate_refs:
            continue
        seen_duplicate_refs.add(duplicate_ref)
        bindings = tuple(
            _candidate_binding(
                source,
                record_by_source,
                frozen_companions,
            )
            for source in candidates
        )
        items.append(
            ReviewSessionItem(
                review_ref=duplicate_ref,
                kind=ReviewItemKind.DUPLICATE,
                state=ReviewItemState.PENDING,
                show_key=_show_key(record),
                duplicate_ref=duplicate_ref,
                candidate_set_sha256=duplicate_candidate_set_hash(
                    destination_key,
                    bindings,
                ),
                candidates=tuple(
                    sorted(candidates, key=lambda value: (normalize_review_path(value), value))
                ),
            )
        )

    for record in records:
        if record.get("status") != "held":
            continue
        source = _source_path(record)
        items.append(
            ReviewSessionItem(
                review_ref=stable_held_ref(source),
                kind=ReviewItemKind.HELD,
                state=ReviewItemState.PENDING,
                show_key=_show_key(record),
                source=source,
            )
        )

    return ReviewSession(
        schema_version=REVIEW_SESSION_SCHEMA_VERSION,
        plan_sha256=manifest_sha256(manifest),
        base_override_snapshot=base_override_snapshot,
        items=tuple(sorted(items, key=lambda item: item.review_ref)),
    )


def render_review_session(session: ReviewSession) -> bytes:
    return session.canonical_bytes + b"\n"


def load_review_session(payload: bytes) -> ReviewSession:
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid review session JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("review session root must be an object")
    allowed = {"schema_version", "plan_sha256", "base_override_snapshot", "items"}
    if set(raw) != allowed:
        raise ValueError("review session has unexpected fields")
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("review session items must be an array")
    items = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            raise ValueError("review session item must be an object")
        try:
            kind = ReviewItemKind(entry.get("kind"))
            state = ReviewItemState(entry.get("state"))
        except ValueError as exc:
            raise ValueError("review session item kind/state is invalid") from exc
        data = entry.get("data", {})
        if not isinstance(data, dict):
            raise ValueError("review session item data must be an object")
        candidates = entry.get("candidates", [])
        if not isinstance(candidates, list) or not all(
            isinstance(candidate, str) for candidate in candidates
        ):
            raise ValueError("review session candidates must be strings")
        items.append(
            ReviewSessionItem(
                review_ref=cast(str, entry.get("review_ref")),
                kind=kind,
                state=state,
                show_key=cast(str, entry.get("show_key")),
                source=cast(str | None, entry.get("source")),
                duplicate_ref=cast(str | None, entry.get("duplicate_ref")),
                candidate_set_sha256=cast(
                    str | None, entry.get("candidate_set_sha256")
                ),
                candidates=tuple(candidates),
                action=cast(str | None, entry.get("action")),
                data_json=json.dumps(
                    data,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    return ReviewSession(
        schema_version=cast(int, raw.get("schema_version")),
        plan_sha256=cast(str, raw.get("plan_sha256")),
        base_override_snapshot=cast(str, raw.get("base_override_snapshot")),
        items=tuple(items),
    )


def atomic_write_new(path: Path, payload: bytes) -> None:
    """Publish one new artifact without overwriting an existing path."""

    if path.exists():
        raise FileExistsError("review output already exists")
    path.parent.mkdir(parents=False, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
            temp.unlink()
        except (AttributeError, NotImplementedError, OSError):
            if path.exists():
                raise FileExistsError("review output already exists")
            os.rename(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_replace(path: Path, payload: bytes) -> None:
    """Atomically replace an existing resumable session with another valid session."""

    path.parent.mkdir(parents=False, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
