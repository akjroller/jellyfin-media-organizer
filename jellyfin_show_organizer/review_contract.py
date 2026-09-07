from __future__ import annotations

import hashlib
import json
import tomllib
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

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


class DuplicateGroupAction(StrEnum):
    SELECT_WINNER = "select_winner"
    KEEP_ALL = "keep_all"


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
        if not self.duplicate_ref.startswith("duplicate-") or len(self.duplicate_ref) != 26:
            raise ValueError("duplicate group decision has an invalid duplicate_ref")
        object.__setattr__(
            self,
            "candidate_set_sha256",
            _sha256(self.candidate_set_sha256, "candidate_set_sha256"),
        )
        if len(self.candidates) < 2:
            raise ValueError("duplicate group decision requires at least two candidates")
        keys = [normalize_review_path(candidate) for candidate in self.candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate group decision candidates must be unique")
        if self.action is DuplicateGroupAction.SELECT_WINNER:
            if self.winner is None or normalize_review_path(self.winner) not in set(keys):
                raise ValueError("selected duplicate winner must be one reviewed candidate")
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
    """Schema-5 active planner state produced by the review system."""

    duplicate_group_decisions: tuple[DuplicateGroupDecision, ...] = ()
    review_session_sha256: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.review_session_sha256 is not None:
            object.__setattr__(
                self,
                "review_session_sha256",
                _sha256(self.review_session_sha256, "review_session_sha256"),
            )

        refs: set[str] = set()
        candidate_sources: set[str] = set()
        for decision in self.duplicate_group_decisions:
            if decision.duplicate_ref in refs:
                raise ValueError("duplicate group decision reference is configured twice")
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

    def duplicate_group_for_ref(self, duplicate_ref: str) -> DuplicateGroupDecision | None:
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


def load_review_contract(path: Path | None = None) -> OverrideCatalog:
    """Load legacy overrides or the full schema-5 review contract."""

    if path is None:
        return _base.load_overrides(None)
    payload = path.read_bytes()
    try:
        raw = tomllib.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("override file must be valid UTF-8") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid override TOML: {exc}") from exc

    if raw.get("schema_version") != REVIEW_OVERRIDE_SCHEMA_VERSION:
        return _base.load_overrides(path)

    allowed_top_level = {
        "schema_version",
        "review_session_sha256",
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

    shows_raw = raw.get("shows", [])
    duplicate_raw = raw.get("duplicate_preferences", [])
    group_raw = raw.get("duplicate_group_decisions", [])
    episode_raw = raw.get("episode_decisions", [])
    holds_raw = raw.get("source_holds", [])
    reviewed_raw = raw.get("reviewed_episode_decisions", [])
    extras_raw = raw.get("extra_decisions", [])
    for label, value in (
        ("shows", shows_raw),
        ("duplicate_preferences", duplicate_raw),
        ("duplicate_group_decisions", group_raw),
        ("episode_decisions", episode_raw),
        ("source_holds", holds_raw),
        ("reviewed_episode_decisions", reviewed_raw),
        ("extra_decisions", extras_raw),
    ):
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError(f"override {label} must be an array of tables")

    groups = tuple(_parse_group_decision(item) for item in group_raw)
    legacy_preferences = tuple(
        _base._parse_duplicate_preference(item) for item in duplicate_raw
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

    session_hash = raw.get("review_session_sha256")
    if session_hash is not None and not isinstance(session_hash, str):
        raise ValueError("review_session_sha256 must be a string")

    return ReviewContractCatalog(
        schema_version=REVIEW_OVERRIDE_SCHEMA_VERSION,
        shows=tuple(shows),
        duplicate_preferences=_compiled_preferences(legacy_preferences, groups),
        episode_decisions=tuple(
            _base._parse_episode_decision(item) for item in episode_raw
        ),
        source_holds=tuple(_base._parse_source_hold(item) for item in holds_raw),
        reviewed_episode_decisions=tuple(
            _parse_reviewed_episode(item) for item in reviewed_raw
        ),
        extra_decisions=tuple(_parse_extra_decision(item) for item in extras_raw),
        show_jellyfin_identifiers=tuple(identifiers),
        duplicate_group_decisions=groups,
        review_session_sha256=session_hash,
    )
