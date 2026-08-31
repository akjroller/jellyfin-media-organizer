"""Immutable data contracts for the plan-first Jellyfin show organizer.

This module deliberately contains no filesystem or network behavior.  It is the
shared boundary between scanning, parsing, matching, reporting, and an eventual
approval-gated apply command.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self, cast

PLAN_SCHEMA_VERSION = 1
VIDEO_EXTENSIONS = frozenset({".avi", ".mkv", ".mp4"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class NumberingMode(StrEnum):
    """How a release's episode identity should be interpreted."""

    AIRED = "aired"
    ABSOLUTE = "absolute"
    PARENTHESIZED_ABSOLUTE = "parenthesized-absolute"
    SEGMENT_TITLE = "segment-title"


class TitlePreference(StrEnum):
    """How episode titles participate in matching for an overridden show."""

    NUMBER_FIRST = "number-first"
    TITLE_FIRST = "title-first"
    TITLE_REQUIRED = "title-required"


class MatchMethod(StrEnum):
    """The primary strategy that produced an episode match."""

    EXPLICIT_ID = "explicit-id"
    MANUAL_OVERRIDE = "manual-override"
    SEASON_EPISODE = "season-episode"
    ABSOLUTE = "absolute"
    EPISODE_TITLE = "episode-title"
    CARTOON_SEGMENT_TITLE = "cartoon-segment-title"


class PlanStatus(StrEnum):
    """The terminal planning status for one source video."""

    MATCHED = "matched"
    UNRESOLVED = "unresolved"
    SUSPICIOUS = "suspicious"
    EXTRA = "extra"
    DUPLICATE = "duplicate"
    SKIPPED = "skipped"


class DuplicateDisposition(StrEnum):
    """Whether a duplicate proposal is actionable or still needs review."""

    PROPOSED = "proposed"
    REVIEW_REQUIRED = "review-required"


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_non_negative(value: int, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_positive(value: int, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sorted_data(values: Sequence[object]) -> list[object]:
    return sorted(values, key=_canonical_json)


def _sorted_evidence(
    values: Sequence[MatchEvidence],
) -> tuple[MatchEvidence, ...]:
    return tuple(sorted(values, key=lambda item: _canonical_json(item.to_data())))


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """Source facts which can be revalidated before a future move."""

    size_bytes: int
    modified_ns: int
    sha256: str | None = None

    def __post_init__(self) -> None:
        _require_non_negative(self.size_bytes, "size_bytes")
        _require_non_negative(self.modified_ns, "modified_ns")
        if self.sha256 is not None:
            normalized = self.sha256.lower()
            if _SHA256_PATTERN.fullmatch(normalized) is None:
                raise ValueError("sha256 must contain exactly 64 hexadecimal digits")
            object.__setattr__(self, "sha256", normalized)

    def to_data(self) -> dict[str, object]:
        return {
            "modified_ns": self.modified_ns,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class SourceVideo:
    """A video discovered beneath the configured Shows root."""

    source_path: str
    extension: str
    fingerprint: SourceFingerprint

    def __post_init__(self) -> None:
        _require_text(self.source_path, "source_path")
        normalized_extension = self.extension.lower()
        if not normalized_extension.startswith("."):
            normalized_extension = f".{normalized_extension}"
        if normalized_extension not in VIDEO_EXTENSIONS:
            raise ValueError(f"unsupported video extension: {self.extension}")
        object.__setattr__(self, "extension", normalized_extension)

    def to_data(self) -> dict[str, object]:
        return {
            "extension": self.extension,
            "fingerprint": self.fingerprint.to_data(),
            "source_path": self.source_path,
        }


@dataclass(frozen=True, slots=True)
class ParsedEpisode:
    """Locally parsed release metadata before provider matching."""

    series: str
    season: int | None = None
    episodes: tuple[int, ...] = ()
    absolute_episode: int | None = None
    parenthesized_absolute_episode: int | None = None
    episode_title: str | None = None
    release_year: int | None = None
    embedded_tvmaze_id: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.series, "series")
        normalized_episodes = tuple(sorted(set(self.episodes)))
        for episode in normalized_episodes:
            _require_positive(episode, "episodes")
        object.__setattr__(self, "episodes", normalized_episodes)

        if self.season is not None:
            _require_non_negative(self.season, "season")
        if self.absolute_episode is not None:
            _require_positive(self.absolute_episode, "absolute_episode")
        if self.parenthesized_absolute_episode is not None:
            _require_positive(
                self.parenthesized_absolute_episode,
                "parenthesized_absolute_episode",
            )
        if self.release_year is not None and not 1800 <= self.release_year <= 3000:
            raise ValueError("release_year must be between 1800 and 3000")
        if self.embedded_tvmaze_id is not None:
            _require_positive(self.embedded_tvmaze_id, "embedded_tvmaze_id")

    def to_data(self) -> dict[str, object]:
        return {
            "absolute_episode": self.absolute_episode,
            "embedded_tvmaze_id": self.embedded_tvmaze_id,
            "episode_title": self.episode_title,
            "episodes": list(self.episodes),
            "parenthesized_absolute_episode": self.parenthesized_absolute_episode,
            "release_year": self.release_year,
            "season": self.season,
            "series": self.series,
        }


@dataclass(frozen=True, slots=True)
class CanonicalShow:
    """One provider-backed show selected for a normalized source group."""

    tvmaze_id: int
    name: str
    year: int | None = None

    def __post_init__(self) -> None:
        _require_positive(self.tvmaze_id, "tvmaze_id")
        _require_text(self.name, "name")
        if self.year is not None and not 1800 <= self.year <= 3000:
            raise ValueError("year must be between 1800 and 3000")

    def to_data(self) -> dict[str, object]:
        return {"name": self.name, "tvmaze_id": self.tvmaze_id, "year": self.year}


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    """One explainable signal contributing to a match decision."""

    signal: str
    value: str
    score: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.signal, "signal")
        _require_text(self.value, "value")
        if self.score is not None and (
            not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0
        ):
            raise ValueError("evidence score must be a finite value from 0 to 1")

    def to_data(self) -> dict[str, object]:
        return {"score": self.score, "signal": self.signal, "value": self.value}


@dataclass(frozen=True, slots=True)
class EpisodeMatch:
    """The canonical episode selected for a source video."""

    show: CanonicalShow
    season: int
    episode: int
    title: str
    method: MatchMethod
    confidence: float
    evidence: tuple[MatchEvidence, ...] = ()

    def __post_init__(self) -> None:
        _require_non_negative(self.season, "season")
        _require_positive(self.episode, "episode")
        _require_text(self.title, "title")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be a finite value from 0 to 1")
        object.__setattr__(self, "method", MatchMethod(self.method))
        object.__setattr__(self, "evidence", _sorted_evidence(self.evidence))

    def to_data(self) -> dict[str, object]:
        evidence = _sorted_data([item.to_data() for item in self.evidence])
        return {
            "confidence": self.confidence,
            "episode": self.episode,
            "evidence": evidence,
            "method": self.method.value,
            "season": self.season,
            "show": self.show.to_data(),
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class ExtraClassification:
    """A non-episode video intentionally excluded from episode matching."""

    kind: str
    evidence: tuple[MatchEvidence, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.kind, "kind")
        object.__setattr__(self, "evidence", _sorted_evidence(self.evidence))

    def to_data(self) -> dict[str, object]:
        return {
            "evidence": _sorted_data([item.to_data() for item in self.evidence]),
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    """A non-destructive proposal for sources sharing one destination."""

    group_id: str
    destination: str
    candidates: tuple[str, ...]
    disposition: DuplicateDisposition
    reason: str
    winner_source: str | None = None
    quarantine_sources: tuple[str, ...] = ()
    evidence: tuple[MatchEvidence, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.group_id, "group_id")
        _require_text(self.destination, "destination")
        _require_text(self.reason, "reason")
        object.__setattr__(self, "disposition", DuplicateDisposition(self.disposition))

        candidates = tuple(
            sorted(set(self.candidates), key=lambda value: (value.casefold(), value))
        )
        if len(candidates) < 2:
            raise ValueError("duplicate decisions require at least two candidates")
        object.__setattr__(self, "candidates", candidates)

        quarantine = tuple(
            sorted(
                set(self.quarantine_sources),
                key=lambda value: (value.casefold(), value),
            )
        )
        if self.winner_source is not None and self.winner_source not in candidates:
            raise ValueError("winner_source must be one of the duplicate candidates")
        if any(source not in candidates for source in quarantine):
            raise ValueError("quarantine_sources must be duplicate candidates")
        if self.winner_source in quarantine:
            raise ValueError("winner_source cannot also be quarantined")
        if (
            self.disposition is DuplicateDisposition.PROPOSED
            and self.winner_source is None
        ):
            raise ValueError("a proposed duplicate decision requires a winner_source")
        object.__setattr__(self, "quarantine_sources", quarantine)
        object.__setattr__(self, "evidence", _sorted_evidence(self.evidence))

    def to_data(self) -> dict[str, object]:
        return {
            "candidates": list(self.candidates),
            "destination": self.destination,
            "disposition": self.disposition.value,
            "evidence": _sorted_data([item.to_data() for item in self.evidence]),
            "group_id": self.group_id,
            "quarantine_sources": list(self.quarantine_sources),
            "reason": self.reason,
            "winner_source": self.winner_source,
        }


@dataclass(frozen=True, slots=True)
class PlanItem:
    """The complete planning result for one source video."""

    source: SourceVideo
    parsed: ParsedEpisode
    status: PlanStatus
    destination: str | None = None
    match: EpisodeMatch | None = None
    extra: ExtraClassification | None = None
    duplicate_group_id: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", PlanStatus(self.status))
        object.__setattr__(self, "notes", tuple(sorted(set(self.notes))))

        if self.status is PlanStatus.MATCHED and (
            self.match is None or self.destination is None
        ):
            raise ValueError("matched plan items require a match and destination")
        if self.status is PlanStatus.EXTRA and self.extra is None:
            raise ValueError("extra plan items require an extra classification")
        if self.status is not PlanStatus.EXTRA and self.extra is not None:
            raise ValueError(
                "only extra plan items may include an extra classification"
            )
        if self.status is PlanStatus.DUPLICATE and self.duplicate_group_id is None:
            raise ValueError("duplicate plan items require duplicate_group_id")
        if (
            self.status is not PlanStatus.DUPLICATE
            and self.duplicate_group_id is not None
        ):
            raise ValueError(
                "only duplicate plan items may reference a duplicate group"
            )
        if self.match is None and self.destination is not None:
            raise ValueError("a destination requires an episode match")

    def to_data(self) -> dict[str, object]:
        return {
            "destination": self.destination,
            "duplicate_group_id": self.duplicate_group_id,
            "extra": self.extra.to_data() if self.extra is not None else None,
            "match": self.match.to_data() if self.match is not None else None,
            "notes": list(self.notes),
            "parsed": self.parsed.to_data(),
            "source": self.source.to_data(),
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class OrganizerPlan:
    """A versioned, immutable plan whose canonical payload has a stable hash."""

    source_root: str
    overrides_version: int
    items: tuple[PlanItem, ...]
    duplicate_decisions: tuple[DuplicateDecision, ...] = ()
    schema_version: int = field(default=PLAN_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        _require_text(self.source_root, "source_root")
        _require_positive(self.overrides_version, "overrides_version")
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "duplicate_decisions", tuple(self.duplicate_decisions))

        source_keys = [item.source.source_path.casefold() for item in self.items]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("plan source paths must be unique case-insensitively")

        group_ids = [decision.group_id for decision in self.duplicate_decisions]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("duplicate decision group IDs must be unique")
        known_groups = set(group_ids)
        referenced_groups = {
            item.duplicate_group_id
            for item in self.items
            if item.duplicate_group_id is not None
        }
        missing_groups = referenced_groups - known_groups
        if missing_groups:
            missing = ", ".join(sorted(missing_groups))
            raise ValueError(
                f"plan items reference unknown duplicate groups: {missing}"
            )

    def canonical_payload(self) -> dict[str, object]:
        """Return deterministic plan data, excluding the derived hash."""
        items = sorted(
            (item.to_data() for item in self.items),
            key=lambda item: (
                cast(
                    str, cast(dict[str, object], item["source"])["source_path"]
                ).casefold(),
                cast(str, cast(dict[str, object], item["source"])["source_path"]),
            ),
        )
        duplicate_decisions = sorted(
            (decision.to_data() for decision in self.duplicate_decisions),
            key=lambda decision: cast(str, decision["group_id"]),
        )
        return {
            "duplicate_decisions": duplicate_decisions,
            "items": items,
            "overrides_version": self.overrides_version,
            "schema_version": self.schema_version,
            "source_root": self.source_root,
        }

    @property
    def plan_hash(self) -> str:
        """Return the SHA-256 of the canonical UTF-8 plan payload."""
        payload = _canonical_json(self.canonical_payload()).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_manifest(self) -> dict[str, object]:
        """Return a portable manifest including its integrity hash."""
        manifest = self.canonical_payload()
        manifest["plan_hash"] = self.plan_hash
        return manifest

    def to_manifest_json(self) -> str:
        """Return a stable, human-readable manifest representation."""
        return (
            json.dumps(self.to_manifest(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, object]) -> Self:
        """Validate and decode a version 1 manifest, including its hash."""
        schema_version = _integer(manifest.get("schema_version"), "schema_version")
        if schema_version != PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported plan schema version: {schema_version}")

        raw_items = _sequence(manifest.get("items"), "items")
        raw_decisions = _sequence(
            manifest.get("duplicate_decisions"), "duplicate_decisions"
        )
        plan = cls(
            source_root=_text(manifest.get("source_root"), "source_root"),
            overrides_version=_integer(
                manifest.get("overrides_version"), "overrides_version"
            ),
            items=tuple(_decode_plan_item(value) for value in raw_items),
            duplicate_decisions=tuple(
                _decode_duplicate_decision(value) for value in raw_decisions
            ),
        )
        supplied_hash = _text(manifest.get("plan_hash"), "plan_hash")
        if not _SHA256_PATTERN.fullmatch(supplied_hash):
            raise ValueError("plan_hash must contain exactly 64 lowercase hex digits")
        if supplied_hash != plan.plan_hash:
            raise ValueError("plan hash does not match the canonical manifest payload")
        return plan


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return value


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _optional_integer(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field_name)


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a number")
    return float(value)


def _optional_number(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _number(value, field_name)


def _decode_fingerprint(value: object) -> SourceFingerprint:
    data = _mapping(value, "fingerprint")
    return SourceFingerprint(
        size_bytes=_integer(data.get("size_bytes"), "fingerprint.size_bytes"),
        modified_ns=_integer(data.get("modified_ns"), "fingerprint.modified_ns"),
        sha256=_optional_text(data.get("sha256"), "fingerprint.sha256"),
    )


def _decode_source(value: object) -> SourceVideo:
    data = _mapping(value, "source")
    return SourceVideo(
        source_path=_text(data.get("source_path"), "source.source_path"),
        extension=_text(data.get("extension"), "source.extension"),
        fingerprint=_decode_fingerprint(data.get("fingerprint")),
    )


def _decode_parsed(value: object) -> ParsedEpisode:
    data = _mapping(value, "parsed")
    episodes = _sequence(data.get("episodes"), "parsed.episodes")
    return ParsedEpisode(
        series=_text(data.get("series"), "parsed.series"),
        season=_optional_integer(data.get("season"), "parsed.season"),
        episodes=tuple(_integer(item, "parsed.episodes[]") for item in episodes),
        absolute_episode=_optional_integer(
            data.get("absolute_episode"), "parsed.absolute_episode"
        ),
        parenthesized_absolute_episode=_optional_integer(
            data.get("parenthesized_absolute_episode"),
            "parsed.parenthesized_absolute_episode",
        ),
        episode_title=_optional_text(data.get("episode_title"), "parsed.episode_title"),
        release_year=_optional_integer(data.get("release_year"), "parsed.release_year"),
        embedded_tvmaze_id=_optional_integer(
            data.get("embedded_tvmaze_id"), "parsed.embedded_tvmaze_id"
        ),
    )


def _decode_show(value: object) -> CanonicalShow:
    data = _mapping(value, "show")
    return CanonicalShow(
        tvmaze_id=_integer(data.get("tvmaze_id"), "show.tvmaze_id"),
        name=_text(data.get("name"), "show.name"),
        year=_optional_integer(data.get("year"), "show.year"),
    )


def _decode_evidence(value: object) -> MatchEvidence:
    data = _mapping(value, "evidence")
    return MatchEvidence(
        signal=_text(data.get("signal"), "evidence.signal"),
        value=_text(data.get("value"), "evidence.value"),
        score=_optional_number(data.get("score"), "evidence.score"),
    )


def _decode_match(value: object) -> EpisodeMatch:
    data = _mapping(value, "match")
    evidence = _sequence(data.get("evidence"), "match.evidence")
    return EpisodeMatch(
        show=_decode_show(data.get("show")),
        season=_integer(data.get("season"), "match.season"),
        episode=_integer(data.get("episode"), "match.episode"),
        title=_text(data.get("title"), "match.title"),
        method=MatchMethod(_text(data.get("method"), "match.method")),
        confidence=_number(data.get("confidence"), "match.confidence"),
        evidence=tuple(_decode_evidence(item) for item in evidence),
    )


def _decode_extra(value: object) -> ExtraClassification:
    data = _mapping(value, "extra")
    evidence = _sequence(data.get("evidence"), "extra.evidence")
    return ExtraClassification(
        kind=_text(data.get("kind"), "extra.kind"),
        evidence=tuple(_decode_evidence(item) for item in evidence),
    )


def _decode_plan_item(value: object) -> PlanItem:
    data = _mapping(value, "item")
    raw_match = data.get("match")
    raw_extra = data.get("extra")
    raw_notes = _sequence(data.get("notes"), "item.notes")
    return PlanItem(
        source=_decode_source(data.get("source")),
        parsed=_decode_parsed(data.get("parsed")),
        status=PlanStatus(_text(data.get("status"), "item.status")),
        destination=_optional_text(data.get("destination"), "item.destination"),
        match=_decode_match(raw_match) if raw_match is not None else None,
        extra=_decode_extra(raw_extra) if raw_extra is not None else None,
        duplicate_group_id=_optional_text(
            data.get("duplicate_group_id"), "item.duplicate_group_id"
        ),
        notes=tuple(_text(note, "item.notes[]") for note in raw_notes),
    )


def _decode_duplicate_decision(value: object) -> DuplicateDecision:
    data = _mapping(value, "duplicate_decision")
    candidates = _sequence(data.get("candidates"), "duplicate_decision.candidates")
    quarantine = _sequence(
        data.get("quarantine_sources"), "duplicate_decision.quarantine_sources"
    )
    evidence = _sequence(data.get("evidence"), "duplicate_decision.evidence")
    return DuplicateDecision(
        group_id=_text(data.get("group_id"), "duplicate_decision.group_id"),
        destination=_text(data.get("destination"), "duplicate_decision.destination"),
        candidates=tuple(
            _text(item, "duplicate_decision.candidates[]") for item in candidates
        ),
        disposition=DuplicateDisposition(
            _text(data.get("disposition"), "duplicate_decision.disposition")
        ),
        reason=_text(data.get("reason"), "duplicate_decision.reason"),
        winner_source=_optional_text(
            data.get("winner_source"), "duplicate_decision.winner_source"
        ),
        quarantine_sources=tuple(
            _text(item, "duplicate_decision.quarantine_sources[]")
            for item in quarantine
        ),
        evidence=tuple(_decode_evidence(item) for item in evidence),
    )
