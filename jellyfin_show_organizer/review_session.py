from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import cast

from .models import DuplicateCollisionClass
from .review_identity import (
    ReviewCandidateBinding,
    ReviewFingerprint,
    ReviewMemberBinding,
    duplicate_candidate_set_hash,
    normalize_review_path,
    source_binding_hash,
    stable_duplicate_ref,
)
from .schema import PLAN_SCHEMA_VERSION, validate_manifest

REVIEW_SESSION_SCHEMA_VERSION = 3


class ReviewItemKind(StrEnum):
    DUPLICATE = "duplicate"
    HELD = "held"


class ReviewItemState(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    DEFERRED = "deferred"


ReviewCollisionClass = DuplicateCollisionClass


_HELD_ACTIONS = frozenset({"keep_held", "episode", "multi_episode", "special", "extra"})
_DUPLICATE_ACTIONS = frozenset({"select_winner", "keep_all", "quarantine_candidate"})


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_sha256(value: str, label: str) -> str:
    digest = value.casefold()
    if len(digest) != 64:
        raise ValueError(f"{label} must contain 64 hex characters")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must contain 64 hex characters") from exc
    return digest


def _require_reviewable_plan_schema(manifest: object) -> Mapping[str, object]:
    validate_manifest(manifest)
    root = cast(Mapping[str, object], manifest)
    if root.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError(
            f"review requires plan schema v{PLAN_SCHEMA_VERSION}; regenerate the plan "
            "with the current `jmo plan` before review"
        )
    return root


def manifest_sha256(manifest: object) -> str:
    _require_reviewable_plan_schema(manifest)
    return _sha256_bytes(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def manifest_override_snapshot(manifest: object) -> str:
    """Return the exact override snapshot recorded by a reviewable plan."""

    root = _require_reviewable_plan_schema(manifest)
    provenance = root.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("review requires plan provenance")
    snapshot = provenance.get("overrides_snapshot_id")
    if not isinstance(snapshot, str):
        raise ValueError("plan provenance is missing overrides_snapshot_id")
    return _require_sha256(snapshot, "plan provenance overrides_snapshot_id")


def stable_held_ref(source: str) -> str:
    digest = hashlib.sha256(normalize_review_path(source).encode("utf-8")).hexdigest()[
        :16
    ]
    return f"held-{digest}"


def _require_exact_keys(
    data: Mapping[str, object], expected: set[str], label: str
) -> None:
    keys = set(data)
    if keys != expected:
        raise ValueError(
            f"{label} data fields must be exactly {sorted(expected)}; got {sorted(keys)}"
        )


def _require_source_mapping(
    value: object,
    *,
    source: str,
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    raw_source = value.get("source")
    if not isinstance(raw_source, str):
        raise ValueError(f"{label}.source must be a string")
    if normalize_review_path(raw_source) != normalize_review_path(source):
        raise ValueError(f"{label}.source does not match the reviewed held source")
    return cast(Mapping[str, object], value)


def _validate_duplicate_answer(
    action: str,
    data: Mapping[str, object],
    candidates: tuple[str, ...],
    collision_class: ReviewCollisionClass,
) -> None:
    if action not in _DUPLICATE_ACTIONS:
        raise ValueError("answered duplicate review item has an invalid action")

    normalized_candidates = {
        normalize_review_path(candidate): candidate for candidate in candidates
    }
    if action == "keep_all":
        _require_exact_keys(data, {"active_action"}, "keep_all")
        if data.get("active_action") != "keep_all":
            raise ValueError("keep_all requires active_action='keep_all'")
        return

    if collision_class is ReviewCollisionClass.DESTINATION_CONFLICT:
        raise ValueError(
            "destination-conflict review items cannot select a duplicate winner"
        )

    expected = {"active_action", "winner"}
    if action == "quarantine_candidate":
        expected.add("quarantine_candidates")
    _require_exact_keys(data, expected, action)
    if data.get("active_action") != "select_winner":
        raise ValueError(f"{action} requires active_action='select_winner'")

    winner = data.get("winner")
    if not isinstance(winner, str):
        raise ValueError(f"{action} requires a winner string")
    winner_key = normalize_review_path(winner)
    if winner_key not in normalized_candidates:
        raise ValueError(f"{action} winner must be one reviewed candidate")

    if action != "quarantine_candidate":
        return
    raw_quarantine = data.get("quarantine_candidates")
    if not isinstance(raw_quarantine, list) or not raw_quarantine:
        raise ValueError(
            "quarantine_candidate requires a non-empty quarantine_candidates list"
        )
    if not all(isinstance(candidate, str) for candidate in raw_quarantine):
        raise ValueError("quarantine_candidates must contain strings")
    quarantine = cast(list[str], raw_quarantine)
    normalized = [normalize_review_path(candidate) for candidate in quarantine]
    if len(normalized) != len(set(normalized)):
        raise ValueError("quarantine_candidates must be unique")
    if winner_key in normalized:
        raise ValueError("duplicate winner cannot also be a quarantine candidate")
    if any(candidate not in normalized_candidates for candidate in normalized):
        raise ValueError("quarantine_candidates must be reviewed candidates")


def _validate_held_answer(
    action: str,
    data: Mapping[str, object],
    source: str,
) -> None:
    if action not in _HELD_ACTIONS:
        raise ValueError("answered held review item has an invalid action")
    if action == "keep_held":
        _require_exact_keys(data, set(), "keep_held")
        return
    if action in {"episode", "special"}:
        _require_exact_keys(data, {"reviewed_episode", "show"}, action)
        _require_source_mapping(
            data.get("reviewed_episode"),
            source=source,
            label="reviewed_episode",
        )
        if not isinstance(data.get("show"), Mapping):
            raise ValueError(f"{action} show metadata must be an object")
        return
    if action == "multi_episode":
        _require_exact_keys(data, {"reviewed_episodes", "show"}, action)
        reviewed = data.get("reviewed_episodes")
        if not isinstance(reviewed, list) or len(reviewed) < 2:
            raise ValueError(
                "multi_episode requires at least two reviewed provider episodes"
            )
        for index, decision in enumerate(reviewed):
            _require_source_mapping(
                decision,
                source=source,
                label=f"reviewed_episodes[{index}]",
            )
        if not isinstance(data.get("show"), Mapping):
            raise ValueError("multi_episode show metadata must be an object")
        return
    _require_exact_keys(data, {"extra", "show"}, "extra")
    _require_source_mapping(data.get("extra"), source=source, label="extra")
    if not isinstance(data.get("show"), Mapping):
        raise ValueError("extra show metadata must be an object")


@dataclass(frozen=True, slots=True)
class ReviewSessionItem:
    review_ref: str
    kind: ReviewItemKind
    state: ReviewItemState
    show_key: str
    source: str | None = None
    source_binding_sha256: str | None = None
    duplicate_ref: str | None = None
    candidate_set_sha256: str | None = None
    candidates: tuple[str, ...] = ()
    collision_class: ReviewCollisionClass | None = None
    action: str | None = None
    data_json: str = "{}"

    def __post_init__(self) -> None:
        if not self.review_ref or not self.show_key:
            raise ValueError("review session items require review_ref and show_key")
        if self.kind is ReviewItemKind.HELD:
            if (
                self.source is None
                or self.source_binding_sha256 is None
                or self.duplicate_ref is not None
                or self.candidate_set_sha256 is not None
                or self.candidates
                or self.collision_class is not None
            ):
                raise ValueError("held review item has invalid identity fields")
            object.__setattr__(
                self,
                "source_binding_sha256",
                _require_sha256(
                    self.source_binding_sha256,
                    "held source_binding_sha256",
                ),
            )
        elif (
            self.source is not None
            or self.source_binding_sha256 is not None
            or self.duplicate_ref is None
            or self.candidate_set_sha256 is None
            or len(self.candidates) < 2
            or self.collision_class is None
        ):
            raise ValueError("duplicate review item has invalid identity fields")
        else:
            object.__setattr__(
                self,
                "candidate_set_sha256",
                _require_sha256(
                    self.candidate_set_sha256,
                    "duplicate candidate_set_sha256",
                ),
            )

        try:
            data = json.loads(self.data_json)
        except json.JSONDecodeError as exc:
            raise ValueError("review item data_json must contain valid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("review item data_json must contain a JSON object")
        object.__setattr__(
            self,
            "data_json",
            json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

        if self.state is ReviewItemState.PENDING:
            if self.action is not None:
                raise ValueError("pending review items cannot carry an action")
            if data:
                raise ValueError("pending review items cannot carry decision data")
            return

        if self.state is ReviewItemState.DEFERRED:
            if self.action != "defer":
                raise ValueError("deferred review items must carry action='defer'")
            if data:
                raise ValueError("deferred review items cannot carry decision data")
            return

        if self.action is None:
            raise ValueError("answered review items require an action")
        if self.kind is ReviewItemKind.DUPLICATE:
            assert self.collision_class is not None
            _validate_duplicate_answer(
                self.action,
                data,
                self.candidates,
                self.collision_class,
            )
            return
        assert self.source is not None
        _validate_held_answer(self.action, data, self.source)

    @property
    def data(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.data_json))

    @property
    def identity_sha256(self) -> str:
        if self.kind is ReviewItemKind.DUPLICATE:
            assert self.candidate_set_sha256 is not None
            return self.candidate_set_sha256
        assert self.source_binding_sha256 is not None
        return self.source_binding_sha256


@dataclass(frozen=True, slots=True)
class ReviewSession:
    schema_version: int
    plan_sha256: str
    base_override_snapshot: str
    base_override_toml: str
    items: tuple[ReviewSessionItem, ...]
    approved_scope_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != REVIEW_SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported review session schema version")
        object.__setattr__(
            self,
            "plan_sha256",
            _require_sha256(self.plan_sha256, "plan_sha256"),
        )
        object.__setattr__(
            self,
            "base_override_snapshot",
            _require_sha256(
                self.base_override_snapshot,
                "base_override_snapshot",
            ),
        )
        if not isinstance(self.base_override_toml, str) or not self.base_override_toml:
            raise ValueError("review session requires the base override TOML payload")
        refs = [item.review_ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("review session refs must be unique")
        approved = tuple(sorted(set(self.approved_scope_refs)))
        if approved != self.approved_scope_refs:
            raise ValueError("approved review scope refs must be unique and sorted")
        known = set(refs)
        if set(approved) - known:
            raise ValueError("approved review scope references an unknown review item")
        for ref in approved:
            if self.item(ref).state is not ReviewItemState.ANSWERED:
                raise ValueError(
                    "approved review scope may contain only answered items"
                )

    @property
    def canonical_bytes(self) -> bytes:
        payload = {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "base_override_snapshot": self.base_override_snapshot,
            "base_override_toml": self.base_override_toml,
            "approved_scope_refs": list(self.approved_scope_refs),
            "items": [
                {
                    "action": item.action,
                    "candidate_set_sha256": item.candidate_set_sha256,
                    "candidates": list(item.candidates),
                    "collision_class": (
                        item.collision_class.value
                        if item.collision_class is not None
                        else None
                    ),
                    "data": item.data,
                    "duplicate_ref": item.duplicate_ref,
                    "kind": item.kind.value,
                    "review_ref": item.review_ref,
                    "show_key": item.show_key,
                    "source": item.source,
                    "source_binding_sha256": item.source_binding_sha256,
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

    @property
    def complete(self) -> bool:
        return all(item.state is ReviewItemState.ANSWERED for item in self.items)

    @property
    def approved_partial(self) -> bool:
        return bool(self.approved_scope_refs) and not self.complete

    @property
    def usable_for_planning(self) -> bool:
        """Allow non-mutating planning; this never authorizes media movement."""

        return self.complete or self.approved_partial

    def item(self, review_ref: str) -> ReviewSessionItem:
        match = next(
            (item for item in self.items if item.review_ref == review_ref), None
        )
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
        approved = tuple(
            ref
            for ref in self.approved_scope_refs
            if next(item for item in updated if item.review_ref == ref).state
            is ReviewItemState.ANSWERED
        )
        return replace(self, items=updated, approved_scope_refs=approved)

    def with_approved_scope(self, review_refs: tuple[str, ...]) -> ReviewSession:
        refs = tuple(sorted(set(review_refs)))
        if not refs:
            raise ValueError("partial approval requires a non-empty explicit scope")
        for ref in refs:
            if self.item(ref).state is not ReviewItemState.ANSWERED:
                raise ValueError("partial approval scope contains an unanswered item")
        return replace(self, approved_scope_refs=refs)


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
    return _source_path(record).replace("\\", "/").split("/", 1)[0]


def _candidate_binding(
    source: str,
    record_by_source: Mapping[str, Mapping[str, object]],
    companions_by_source: Mapping[str, tuple[Mapping[str, object], ...]],
) -> ReviewCandidateBinding:
    record = record_by_source.get(normalize_review_path(source))
    if record is None:
        raise ValueError("review source is missing its plan record")
    source_raw = _mapping(record.get("source"), "record.source")
    source_member = ReviewMemberBinding(
        path=source,
        fingerprint=_fingerprint(
            source_raw.get("fingerprint"), "record.source.fingerprint"
        ),
    )
    companions: list[ReviewMemberBinding] = []
    for companion in companions_by_source.get(normalize_review_path(source), ()):
        path = companion.get("relative_path")
        if not isinstance(path, str) or not path:
            raise ValueError("companion relative_path is invalid")
        fingerprint = companion.get("fingerprint")
        if fingerprint is not None:
            companions.append(
                ReviewMemberBinding(
                    path=path,
                    fingerprint=_fingerprint(fingerprint, "companion.fingerprint"),
                )
            )
    return ReviewCandidateBinding(
        source=source_member,
        companions=tuple(
            sorted(
                companions,
                key=lambda member: (normalize_review_path(member.path), member.path),
            )
        ),
    )


def _collision_class(duplicate: Mapping[str, object]) -> ReviewCollisionClass:
    value = duplicate.get("collision_class")
    if not isinstance(value, str):
        raise ValueError("duplicate decision is missing structured collision_class")
    try:
        return ReviewCollisionClass(value)
    except ValueError as exc:
        raise ValueError("duplicate decision collision_class is invalid") from exc


def build_review_session(
    manifest: object,
    *,
    base_override_snapshot: str,
    base_override_payload: bytes,
) -> ReviewSession:
    root = _require_reviewable_plan_schema(manifest)
    try:
        base_override_toml = base_override_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("base override payload must be valid UTF-8") from exc
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
            companions_by_source.setdefault(
                normalize_review_path(source_video), []
            ).append(companion)
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
            _candidate_binding(source, record_by_source, frozen_companions)
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
                    sorted(
                        candidates,
                        key=lambda value: (normalize_review_path(value), value),
                    )
                ),
                collision_class=_collision_class(duplicate),
            )
        )

    for record in records:
        if record.get("status") not in {"held", "suspicious", "unresolved"}:
            continue
        if isinstance(record.get("duplicate"), Mapping):
            continue
        source = _source_path(record)
        binding = _candidate_binding(source, record_by_source, frozen_companions)
        items.append(
            ReviewSessionItem(
                review_ref=stable_held_ref(source),
                kind=ReviewItemKind.HELD,
                state=ReviewItemState.PENDING,
                show_key=_show_key(record),
                source=source,
                source_binding_sha256=source_binding_hash(binding),
            )
        )

    return ReviewSession(
        schema_version=REVIEW_SESSION_SCHEMA_VERSION,
        plan_sha256=manifest_sha256(manifest),
        base_override_snapshot=base_override_snapshot,
        base_override_toml=base_override_toml,
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
    expected = {
        "schema_version",
        "plan_sha256",
        "base_override_snapshot",
        "base_override_toml",
        "approved_scope_refs",
        "items",
    }
    if set(raw) != expected:
        raise ValueError("review session has unexpected fields")
    raw_items = raw.get("items")
    approved_scope_refs = raw.get("approved_scope_refs")
    if not isinstance(raw_items, list):
        raise ValueError("review session items must be an array")
    if not isinstance(approved_scope_refs, list) or not all(
        isinstance(ref, str) for ref in approved_scope_refs
    ):
        raise ValueError("approved review scope refs must be strings")

    items: list[ReviewSessionItem] = []
    expected_item_fields = {
        "action",
        "candidate_set_sha256",
        "candidates",
        "collision_class",
        "data",
        "duplicate_ref",
        "kind",
        "review_ref",
        "show_key",
        "source",
        "source_binding_sha256",
        "state",
    }
    for entry in raw_items:
        if not isinstance(entry, dict):
            raise ValueError("review session item must be an object")
        if set(entry) != expected_item_fields:
            raise ValueError("review session item has unexpected fields")
        raw_kind = entry.get("kind")
        raw_state = entry.get("state")
        if not isinstance(raw_kind, str) or not isinstance(raw_state, str):
            raise ValueError("review session item kind/state must be strings")
        try:
            kind = ReviewItemKind(raw_kind)
            state = ReviewItemState(raw_state)
        except ValueError as exc:
            raise ValueError("review session item kind/state is invalid") from exc
        raw_collision_class = entry.get("collision_class")
        if raw_collision_class is not None and not isinstance(raw_collision_class, str):
            raise ValueError("review session collision_class must be a string")
        try:
            collision_class = (
                ReviewCollisionClass(raw_collision_class)
                if raw_collision_class is not None
                else None
            )
        except ValueError as exc:
            raise ValueError("review session collision_class is invalid") from exc
        data = entry.get("data", {})
        candidates = entry.get("candidates", [])
        if not isinstance(data, dict):
            raise ValueError("review session item data must be an object")
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
                source_binding_sha256=cast(
                    str | None,
                    entry.get("source_binding_sha256"),
                ),
                duplicate_ref=cast(str | None, entry.get("duplicate_ref")),
                candidate_set_sha256=cast(
                    str | None,
                    entry.get("candidate_set_sha256"),
                ),
                candidates=tuple(candidates),
                collision_class=collision_class,
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
        base_override_toml=cast(str, raw.get("base_override_toml")),
        items=tuple(items),
        approved_scope_refs=tuple(cast(list[str], approved_scope_refs)),
    )


def _write_temp(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=False, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return temp


def atomic_write_new(path: Path, payload: bytes) -> None:
    """Publish one new artifact without overwriting an existing path."""

    if path.exists():
        raise FileExistsError("review output already exists")
    temp = _write_temp(path, payload)
    try:
        try:
            os.link(temp, path)
            temp.unlink()
        except (AttributeError, NotImplementedError, OSError) as exc:
            if path.exists():
                raise FileExistsError("review output already exists") from exc
            os.rename(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_replace(path: Path, payload: bytes) -> None:
    """Atomically replace an existing resumable session with another valid session."""

    temp = _write_temp(path, payload)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
