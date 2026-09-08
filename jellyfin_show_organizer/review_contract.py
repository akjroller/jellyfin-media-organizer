from __future__ import annotations

import copy
import hashlib
import json
import tomllib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from . import overrides as _base
from .overrides import DuplicatePreferenceOverride, OverrideCatalog
from .review_identity import normalize_review_path
from .review_overrides import (
    REVIEW_OVERRIDE_SCHEMA_VERSION,
    ReviewOverrideCatalog,
    ShowJellyfinIdentifiers,
    _parse_extra_decision,
    _parse_reviewed_episode,
    _show_ids,
)
from .review_session import ReviewItemKind, ReviewItemState, ReviewSession


class DuplicateGroupAction(StrEnum):
    SELECT_WINNER = "select_winner"
    KEEP_ALL = "keep_all"


_TABLE_ORDER = (
    "shows",
    "duplicate_preferences",
    "duplicate_group_decisions",
    "episode_decisions",
    "source_holds",
    "reviewed_episode_decisions",
    "extra_decisions",
)

_FIELD_ORDER: dict[str, tuple[str, ...]] = {
    "shows": (
        "key",
        "tvmaze_id",
        "provider",
        "provider_id",
        "aliases",
        "year",
        "numbering_mode",
        "title_preference",
        "preferred_title",
        "tmdb_id",
        "tvdb_id",
        "imdb_id",
    ),
    "duplicate_preferences": ("source", "rank", "reasons"),
    "duplicate_group_decisions": (
        "duplicate_ref",
        "candidate_set_sha256",
        "candidates",
        "action",
        "winner",
        "reasons",
    ),
    "episode_decisions": (
        "source",
        "show_provider",
        "show_provider_id",
        "numbering_mode",
        "season",
        "episodes",
        "absolute_episode",
        "special_kind",
        "special_episode",
        "episode_date",
        "segment_hint",
        "title_hint",
        "reasons",
    ),
    "source_holds": ("source", "reasons"),
    "reviewed_episode_decisions": (
        "source",
        "source_binding_sha256",
        "show_provider",
        "show_provider_id",
        "episode_provider",
        "episode_provider_id",
        "season",
        "number",
        "title",
        "airdate",
        "lookup_mode",
        "reasons",
    ),
    "extra_decisions": (
        "source",
        "source_binding_sha256",
        "show_provider",
        "show_provider_id",
        "kind",
        "display_title",
        "reasons",
    ),
}


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a SHA-256 string")
    digest = value.casefold()
    if len(digest) != 64:
        raise ValueError(f"{label} must contain 64 hex characters")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must contain 64 hex characters") from exc
    return digest


@dataclass(frozen=True, slots=True)
class DuplicateGroupDecision:
    """One human-reviewed collision bound to the complete reviewed candidate set."""

    duplicate_ref: str
    candidate_set_sha256: str
    candidates: tuple[str, ...]
    action: DuplicateGroupAction
    winner: str | None = None
    reasons: tuple[str, ...] = ("explicit reviewed duplicate group decision",)

    def __post_init__(self) -> None:
        if (
            not self.duplicate_ref.startswith("duplicate-")
            or len(self.duplicate_ref) != 26
        ):
            raise ValueError("duplicate group decision has an invalid duplicate_ref")
        object.__setattr__(
            self,
            "candidate_set_sha256",
            _sha256(self.candidate_set_sha256, "candidate_set_sha256"),
        )
        if len(self.candidates) < 2:
            raise ValueError(
                "duplicate group decision requires at least two candidates"
            )
        keys = [normalize_review_path(candidate) for candidate in self.candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate group decision candidates must be unique")
        if self.action is DuplicateGroupAction.SELECT_WINNER:
            if self.winner is None or normalize_review_path(self.winner) not in set(
                keys
            ):
                raise ValueError(
                    "selected duplicate winner must be one reviewed candidate"
                )
        elif self.winner is not None:
            raise ValueError("keep-all duplicate decisions cannot carry a winner")
        if not self.reasons or any(
            not reason or reason != reason.strip() for reason in self.reasons
        ):
            raise ValueError(
                "duplicate group decision reasons must contain non-empty trimmed strings"
            )
        normalized_reasons = [
            unicodedata.normalize("NFKC", reason).casefold() for reason in self.reasons
        ]
        if len(normalized_reasons) != len(set(normalized_reasons)):
            raise ValueError("duplicate group decision reasons must be unique")


@dataclass(frozen=True, slots=True)
class ReviewContractCatalog(ReviewOverrideCatalog):
    """Schema-5 active planner state produced by one verifiable review session."""

    duplicate_group_decisions: tuple[DuplicateGroupDecision, ...] = ()
    review_session_sha256: str = ""
    review_base_plan_sha256: str = ""
    review_base_override_snapshot: str = ""

    def __post_init__(self) -> None:
        ReviewOverrideCatalog.__post_init__(self)
        object.__setattr__(
            self,
            "review_session_sha256",
            _sha256(self.review_session_sha256, "review_session_sha256"),
        )
        object.__setattr__(
            self,
            "review_base_plan_sha256",
            _sha256(self.review_base_plan_sha256, "review_base_plan_sha256"),
        )
        object.__setattr__(
            self,
            "review_base_override_snapshot",
            _sha256(
                self.review_base_override_snapshot,
                "review_base_override_snapshot",
            ),
        )

        refs: set[str] = set()
        candidate_sources: set[str] = set()
        for decision in self.duplicate_group_decisions:
            if decision.duplicate_ref in refs:
                raise ValueError(
                    "duplicate group decision reference is configured twice"
                )
            refs.add(decision.duplicate_ref)
            for source in decision.candidates:
                key = normalize_review_path(source)
                if key in candidate_sources:
                    raise ValueError(
                        "one source cannot belong to multiple reviewed duplicate groups"
                    )
                candidate_sources.add(key)

        legacy_sources = {
            normalize_review_path(preference.source)
            for preference in self.duplicate_preferences
            if not any(
                reason.startswith("reviewed-duplicate-ref:")
                for reason in preference.reasons
            )
        }
        if legacy_sources & candidate_sources:
            raise ValueError(
                "reviewed duplicate groups cannot overlap legacy duplicate preferences"
            )

    def duplicate_group_for_ref(
        self, duplicate_ref: str
    ) -> DuplicateGroupDecision | None:
        return next(
            (
                decision
                for decision in self.duplicate_group_decisions
                if decision.duplicate_ref == duplicate_ref
            ),
            None,
        )

    def canonical_bytes(self) -> bytes:
        base = ReviewOverrideCatalog(
            schema_version=self.schema_version,
            shows=self.shows,
            duplicate_preferences=self.duplicate_preferences,
            episode_decisions=self.episode_decisions,
            source_holds=self.source_holds,
            reviewed_episode_decisions=self.reviewed_episode_decisions,
            extra_decisions=self.extra_decisions,
            show_jellyfin_identifiers=self.show_jellyfin_identifiers,
        )
        payload = json.loads(base.canonical_bytes().decode("utf-8"))
        payload["review_session_sha256"] = self.review_session_sha256
        payload["review_base_plan_sha256"] = self.review_base_plan_sha256
        payload["review_base_override_snapshot"] = self.review_base_override_snapshot
        payload["duplicate_group_decisions"] = [
            {
                "action": decision.action.value,
                "candidate_set_sha256": decision.candidate_set_sha256,
                "candidates": sorted(
                    decision.candidates,
                    key=lambda value: (normalize_review_path(value), value),
                ),
                "duplicate_ref": decision.duplicate_ref,
                "reasons": sorted(
                    decision.reasons,
                    key=lambda reason: (
                        unicodedata.normalize("NFKC", reason).casefold(),
                        reason,
                    ),
                ),
                "winner": decision.winner,
            }
            for decision in sorted(
                self.duplicate_group_decisions,
                key=lambda item: item.duplicate_ref,
            )
        ]
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _parse_group_decision(raw: dict[str, Any]) -> DuplicateGroupDecision:
    allowed = {
        "duplicate_ref",
        "candidate_set_sha256",
        "candidates",
        "action",
        "winner",
        "reasons",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown duplicate group decision fields: {sorted(unknown)}")
    duplicate_ref = raw.get("duplicate_ref")
    candidate_set_sha256 = raw.get("candidate_set_sha256")
    candidates = raw.get("candidates")
    action = raw.get("action")
    winner = raw.get("winner")
    reasons = raw.get("reasons", ["explicit reviewed duplicate group decision"])
    if not isinstance(duplicate_ref, str):
        raise ValueError("duplicate group decision duplicate_ref must be a string")
    if not isinstance(candidate_set_sha256, str):
        raise ValueError("duplicate group candidate_set_sha256 must be a string")
    if not isinstance(candidates, list) or not all(
        isinstance(candidate, str) for candidate in candidates
    ):
        raise ValueError("duplicate group candidates must be a list of strings")
    if not isinstance(action, str):
        raise ValueError("duplicate group action must be a string")
    if winner is not None and not isinstance(winner, str):
        raise ValueError("duplicate group winner must be a string")
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ValueError("duplicate group reasons must be a list of strings")
    try:
        parsed_action = DuplicateGroupAction(action)
    except ValueError as exc:
        raise ValueError("duplicate group action is invalid") from exc
    return DuplicateGroupDecision(
        duplicate_ref=duplicate_ref,
        candidate_set_sha256=candidate_set_sha256,
        candidates=tuple(candidates),
        action=parsed_action,
        winner=winner,
        reasons=tuple(reasons),
    )


def _compiled_preferences(
    legacy: tuple[DuplicatePreferenceOverride, ...],
    groups: tuple[DuplicateGroupDecision, ...],
) -> tuple[DuplicatePreferenceOverride, ...]:
    compiled = list(legacy)
    for group in groups:
        if group.action is not DuplicateGroupAction.SELECT_WINNER:
            continue
        assert group.winner is not None
        compiled.append(
            DuplicatePreferenceOverride(
                source=group.winner,
                rank=1_000_000,
                reasons=(
                    "authoritative reviewed duplicate-group winner",
                    f"reviewed-duplicate-ref:{group.duplicate_ref}",
                    f"reviewed-candidate-set:{group.candidate_set_sha256}",
                    *group.reasons,
                ),
            )
        )
    return tuple(compiled)


def _raw_override(payload: bytes) -> dict[str, Any]:
    try:
        raw = tomllib.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("override file must be valid UTF-8") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid override TOML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("override root must be a table")
    return cast(dict[str, Any], raw)


@dataclass(frozen=True, slots=True)
class _PayloadPathAdapter:
    """Give the canonical legacy Path loader an in-memory payload."""

    payload: bytes

    def read_bytes(self) -> bytes:
        return self.payload


def _load_legacy_payload(payload: bytes) -> OverrideCatalog:
    adapter = cast(Path, _PayloadPathAdapter(payload))
    return _base.load_overrides(adapter)


def _array_tables(raw: Mapping[str, object], names: tuple[str, ...]) -> None:
    for label in names:
        value = raw.get(label, [])
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise ValueError(f"override {label} must be an array of tables")


def load_review_contract_payload(payload: bytes) -> OverrideCatalog:
    """Delegate schemas 1-4 to the legacy loader; parse only schema 5 here."""

    raw = _raw_override(payload)
    schema_version = raw.get("schema_version")
    if schema_version != REVIEW_OVERRIDE_SCHEMA_VERSION:
        return _load_legacy_payload(payload)

    allowed_top_level = {
        "schema_version",
        "review_session_sha256",
        "review_base_plan_sha256",
        "review_base_override_snapshot",
        "shows",
        "duplicate_preferences",
        "duplicate_group_decisions",
        "episode_decisions",
        "source_holds",
        "reviewed_episode_decisions",
        "extra_decisions",
    }
    unknown_top_level = set(raw) - allowed_top_level
    if unknown_top_level:
        raise ValueError(
            f"unknown top-level override fields: {sorted(unknown_top_level)}"
        )
    for field in (
        "review_session_sha256",
        "review_base_plan_sha256",
        "review_base_override_snapshot",
    ):
        if not isinstance(raw.get(field), str):
            raise ValueError(f"schema-5 override requires {field}")

    _array_tables(raw, _TABLE_ORDER)
    shows_raw = cast(list[dict[str, Any]], raw.get("shows", []))
    groups = tuple(
        _parse_group_decision(item)
        for item in cast(list[dict[str, Any]], raw.get("duplicate_group_decisions", []))
    )
    legacy_preferences = tuple(
        _base._parse_duplicate_preference(item)
        for item in cast(list[dict[str, Any]], raw.get("duplicate_preferences", []))
    )

    shows = []
    identifiers = []
    for raw_show in shows_raw:
        show_payload = dict(raw_show)
        ids = _show_ids(show_payload)
        for field in ("tmdb_id", "tvdb_id", "imdb_id"):
            show_payload.pop(field, None)
        show = _base._parse_override(show_payload)
        shows.append(show)
        if ids:
            identifiers.append(
                ShowJellyfinIdentifiers(show_key=show.key, identifiers=ids)
            )

    return ReviewContractCatalog(
        schema_version=REVIEW_OVERRIDE_SCHEMA_VERSION,
        shows=tuple(shows),
        duplicate_preferences=_compiled_preferences(legacy_preferences, groups),
        episode_decisions=tuple(
            _base._parse_episode_decision(item)
            for item in cast(list[dict[str, Any]], raw.get("episode_decisions", []))
        ),
        source_holds=tuple(
            _base._parse_source_hold(item)
            for item in cast(list[dict[str, Any]], raw.get("source_holds", []))
        ),
        reviewed_episode_decisions=tuple(
            _parse_reviewed_episode(item)
            for item in cast(
                list[dict[str, Any]], raw.get("reviewed_episode_decisions", [])
            )
        ),
        extra_decisions=tuple(
            _parse_extra_decision(item)
            for item in cast(list[dict[str, Any]], raw.get("extra_decisions", []))
        ),
        show_jellyfin_identifiers=tuple(identifiers),
        duplicate_group_decisions=groups,
        review_session_sha256=cast(str, raw["review_session_sha256"]),
        review_base_plan_sha256=cast(str, raw["review_base_plan_sha256"]),
        review_base_override_snapshot=cast(
            str,
            raw["review_base_override_snapshot"],
        ),
    )


def load_review_contract(path: Path | None = None) -> OverrideCatalog:
    if path is None:
        return _base.load_overrides(None)
    payload = path.read_bytes()
    raw = _raw_override(payload)
    if raw.get("schema_version") != REVIEW_OVERRIDE_SCHEMA_VERSION:
        return _base.load_overrides(path)
    return load_review_contract_payload(payload)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ValueError("unsupported active override value")


def render_active_overrides(raw: Mapping[str, object]) -> bytes:
    lines = ["schema_version = 5"]
    for field in (
        "review_session_sha256",
        "review_base_plan_sha256",
        "review_base_override_snapshot",
    ):
        value = raw.get(field)
        if not isinstance(value, str):
            raise ValueError(f"active review contract requires {field}")
        lines.append(f"{field} = {_toml_value(value)}")
    for table in _TABLE_ORDER:
        values = raw.get(table, [])
        if not isinstance(values, list) or not all(
            isinstance(item, dict) for item in values
        ):
            raise ValueError(f"override {table} must be an array of tables")
        order = _FIELD_ORDER[table]
        for item in cast(list[dict[str, object]], values):
            unknown = set(item) - set(order)
            if unknown:
                raise ValueError(
                    f"override {table} contains unsupported fields: {sorted(unknown)}"
                )
            lines.extend(("", f"[[{table}]]"))
            for field in order:
                if field in item and item[field] is not None:
                    lines.append(f"{field} = {_toml_value(item[field])}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _source_key(value: str) -> str:
    return value.replace("\\", "/").casefold()


def _remove_source(raw: dict[str, Any], table: str, source: str) -> None:
    values = cast(list[dict[str, Any]], raw.setdefault(table, []))
    key = _source_key(source)
    values[:] = [
        item
        for item in values
        if not isinstance(item.get("source"), str)
        or _source_key(cast(str, item["source"])) != key
    ]


def _upsert_show(raw: dict[str, Any], show: Mapping[str, object]) -> None:
    key = show.get("key")
    if not isinstance(key, str):
        raise ValueError("reviewed show metadata is missing its key")
    values = cast(list[dict[str, Any]], raw.setdefault("shows", []))
    normalized = key.casefold()
    values[:] = [
        item
        for item in values
        if not isinstance(item.get("key"), str)
        or cast(str, item["key"]).casefold() != normalized
    ]
    values.append(copy.deepcopy(dict(show)))


def _compile_duplicate_item(raw: dict[str, Any], item: Any) -> None:
    if item.action not in {"select_winner", "keep_all", "quarantine_candidate"}:
        raise ValueError("answered duplicate item has an invalid stored action")
    groups = cast(list[dict[str, Any]], raw.setdefault("duplicate_group_decisions", []))
    groups[:] = [
        group for group in groups if group.get("duplicate_ref") != item.duplicate_ref
    ]
    for candidate in item.candidates:
        _remove_source(raw, "duplicate_preferences", candidate)

    data = item.data
    active_action = data.get("active_action")
    if active_action not in {
        DuplicateGroupAction.SELECT_WINNER.value,
        DuplicateGroupAction.KEEP_ALL.value,
    }:
        raise ValueError("answered duplicate item has no valid active action")
    assert item.duplicate_ref is not None
    assert item.candidate_set_sha256 is not None
    group: dict[str, object] = {
        "duplicate_ref": item.duplicate_ref,
        "candidate_set_sha256": item.candidate_set_sha256,
        "candidates": list(item.candidates),
        "action": active_action,
        "reasons": ["human-reviewed duplicate group decision"],
    }
    winner = data.get("winner")
    if active_action == DuplicateGroupAction.SELECT_WINNER.value:
        if not isinstance(winner, str):
            raise ValueError("reviewed duplicate winner is invalid")
        group["winner"] = winner
    elif winner is not None:
        raise ValueError("keep-all duplicate item cannot carry a winner")
    groups.append(group)


def _clear_source_dispositions(raw: dict[str, Any], source: str) -> None:
    for table in (
        "source_holds",
        "episode_decisions",
        "reviewed_episode_decisions",
        "extra_decisions",
        "duplicate_preferences",
    ):
        _remove_source(raw, table, source)


def _compile_held_item(raw: dict[str, Any], item: Any) -> None:
    assert item.source is not None
    assert item.source_binding_sha256 is not None
    if item.action == "keep_held":
        _clear_source_dispositions(raw, item.source)
        cast(list[dict[str, Any]], raw["source_holds"]).append(
            {
                "source": item.source,
                "reasons": ["human review chose to leave source untouched"],
            }
        )
        return
    if item.action not in {"episode", "multi_episode", "special", "extra"}:
        raise ValueError("answered held item has an invalid stored action")

    data = item.data
    decisions: list[dict[str, Any]]
    if item.action in {"episode", "special"}:
        reviewed_episode = data.get("reviewed_episode")
        if not isinstance(reviewed_episode, Mapping):
            raise ValueError("reviewed held episode has no episode decision")
        decision = copy.deepcopy(dict(reviewed_episode))
        decision["source_binding_sha256"] = item.source_binding_sha256
        decisions = [decision]
        target_table = "reviewed_episode_decisions"
    elif item.action == "multi_episode":
        reviewed_episodes = data.get("reviewed_episodes")
        if not isinstance(reviewed_episodes, list) or len(reviewed_episodes) < 2:
            raise ValueError("reviewed compound source has no provider episode set")
        decisions = []
        for reviewed_episode in reviewed_episodes:
            if not isinstance(reviewed_episode, Mapping):
                raise ValueError("reviewed compound episode entry is invalid")
            decision = copy.deepcopy(dict(reviewed_episode))
            decision["source_binding_sha256"] = item.source_binding_sha256
            decisions.append(decision)
        target_table = "reviewed_episode_decisions"
    else:
        extra = data.get("extra")
        if not isinstance(extra, Mapping):
            raise ValueError("reviewed held extra has no extra decision")
        decision = copy.deepcopy(dict(extra))
        decision["source_binding_sha256"] = item.source_binding_sha256
        decisions = [decision]
        target_table = "extra_decisions"

    _clear_source_dispositions(raw, item.source)
    show = data.get("show")
    if not isinstance(show, Mapping):
        raise ValueError("reviewed held decision is missing show metadata")
    _upsert_show(raw, cast(Mapping[str, object], show))
    cast(list[dict[str, Any]], raw[target_table]).extend(decisions)


def compile_active_overrides(session: ReviewSession) -> bytes:
    """Compile answered ledger items over the exact stored base override state."""

    raw = _raw_override(session.base_override_toml.encode("utf-8"))
    raw.pop("review_session_sha256", None)
    raw.pop("review_base_plan_sha256", None)
    raw.pop("review_base_override_snapshot", None)
    raw.pop("duplicate_group_decisions", None)
    raw.pop("reviewed_episode_decisions", None)
    raw.pop("extra_decisions", None)
    for table in _TABLE_ORDER:
        raw.setdefault(table, [])

    for item in session.items:
        if item.state is not ReviewItemState.ANSWERED:
            continue
        if item.kind is ReviewItemKind.DUPLICATE:
            _compile_duplicate_item(raw, item)
        else:
            _compile_held_item(raw, item)

    raw["schema_version"] = REVIEW_OVERRIDE_SCHEMA_VERSION
    raw["review_session_sha256"] = session.sha256
    raw["review_base_plan_sha256"] = session.plan_sha256
    raw["review_base_override_snapshot"] = session.base_override_snapshot
    payload = render_active_overrides(raw)
    loaded = load_review_contract_payload(payload)
    if not isinstance(loaded, ReviewContractCatalog):
        raise ValueError("compiled review session did not produce schema 5")
    return payload


def verify_review_contract_session(
    catalog: ReviewContractCatalog,
    session: ReviewSession,
) -> None:
    """Fail closed unless the active contract is exactly derived from this ledger."""

    if catalog.review_session_sha256 != session.sha256:
        raise ValueError(
            "active review contract does not match the supplied session hash"
        )
    if catalog.review_base_plan_sha256 != session.plan_sha256:
        raise ValueError("active review contract does not match the session base plan")
    if catalog.review_base_override_snapshot != session.base_override_snapshot:
        raise ValueError(
            "active review contract does not match the session base override snapshot"
        )
    if not session.usable_for_planning:
        raise ValueError(
            "review session still has unresolved work and has no approved partial scope"
        )

    base_catalog = load_review_contract_payload(
        session.base_override_toml.encode("utf-8")
    )
    if base_catalog.snapshot_id != session.base_override_snapshot:
        raise ValueError(
            "review session base override payload no longer matches its snapshot"
        )

    expected_payload = compile_active_overrides(session)
    expected = load_review_contract_payload(expected_payload)
    if not isinstance(expected, ReviewContractCatalog):
        raise ValueError("compiled review session did not produce a schema-5 contract")
    if expected.snapshot_id != catalog.snapshot_id:
        raise ValueError(
            "active review contract decisions do not match the supplied review session"
        )
