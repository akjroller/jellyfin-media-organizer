from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class NumberingMode(StrEnum):
    AIRED = "aired"
    ABSOLUTE = "absolute"
    PARENTHESIZED_ABSOLUTE = "parenthesized-absolute"
    SEGMENT_TITLE = "segment-title"
    SPECIAL = "special"
    DATE = "date"


class TitlePreference(StrEnum):
    PROVIDER = "provider"
    SOURCE = "source"
    OVERRIDE = "override"


class TerminalStatus(StrEnum):
    MATCHED = "matched"
    EXTRA = "extra"
    DUPLICATE = "duplicate"
    SUSPICIOUS = "suspicious"
    UNRESOLVED = "unresolved"


class CompanionStatus(StrEnum):
    ASSOCIATED = "associated"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    size: int
    mtime_ns: int
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.size < 0:
            raise ValueError("fingerprint size cannot be negative")
        if self.mtime_ns < 0:
            raise ValueError("fingerprint mtime_ns cannot be negative")
        if self.sha256 is not None and len(self.sha256) != 64:
            raise ValueError("fingerprint sha256 must contain 64 hex characters")
        if self.sha256 is not None:
            try:
                int(self.sha256, 16)
            except ValueError as exc:
                raise ValueError(
                    "fingerprint sha256 must contain 64 hex characters"
                ) from exc


@dataclass(frozen=True, slots=True)
class SourceFile:
    relative_path: str
    extension: str
    fingerprint: SourceFingerprint

    def __post_init__(self) -> None:
        if not self.relative_path:
            raise ValueError("source relative_path cannot be empty")
        if not self.extension.startswith("."):
            raise ValueError("source extension must start with '.'")


@dataclass(frozen=True, slots=True)
class ParseResult:
    series_hint: str | None = None
    season: int | None = None
    episodes: tuple[int, ...] = ()
    absolute_episode: int | None = None
    special_kind: str | None = None
    special_episode: int | None = None
    episode_date: str | None = None
    segment_hint: str | None = None
    year: int | None = None
    embedded_tvmaze_id: int | None = None
    title_hint: str | None = None

    def __post_init__(self) -> None:
        if self.season is not None and self.season < 0:
            raise ValueError("season cannot be negative")
        if any(episode < 0 for episode in self.episodes):
            raise ValueError("episodes cannot contain negative values")
        if self.absolute_episode is not None and self.absolute_episode < 0:
            raise ValueError("absolute_episode cannot be negative")
        if (self.special_kind is None) != (self.special_episode is None):
            raise ValueError(
                "special_kind and special_episode must be provided together"
            )
        if self.special_kind is not None:
            special_kind = self.special_kind.casefold()
            if special_kind not in {"ova", "oad"}:
                raise ValueError("special_kind must be 'ova' or 'oad'")
            object.__setattr__(self, "special_kind", special_kind)
        if self.special_episode is not None and self.special_episode <= 0:
            raise ValueError("special_episode must be positive")
        if self.episode_date is not None:
            try:
                normalized_date = date.fromisoformat(self.episode_date).isoformat()
            except ValueError as exc:
                raise ValueError(
                    "episode_date must use a valid YYYY-MM-DD date"
                ) from exc
            if normalized_date != self.episode_date:
                raise ValueError("episode_date must use canonical YYYY-MM-DD form")
        if self.year is not None and self.year < 1800:
            raise ValueError("year is outside the supported range")
        if self.embedded_tvmaze_id is not None and self.embedded_tvmaze_id <= 0:
            raise ValueError("embedded_tvmaze_id must be positive")


@dataclass(frozen=True, slots=True)
class CanonicalShow:
    source_key: str
    tvmaze_id: int
    title: str
    year: int | None
    numbering_mode: NumberingMode

    def __post_init__(self) -> None:
        if not self.source_key:
            raise ValueError("canonical show source_key cannot be empty")
        if self.tvmaze_id <= 0:
            raise ValueError("canonical show tvmaze_id must be positive")
        if not self.title:
            raise ValueError("canonical show title cannot be empty")


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    tvmaze_id: int
    title: str
    score: float
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.tvmaze_id <= 0:
            raise ValueError("candidate tvmaze_id must be positive")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("candidate score must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    method: str
    confidence: float
    reasons: tuple[str, ...] = ()
    candidates: tuple[CandidateEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.method:
            raise ValueError("match evidence method cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("match confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ExtraDecision:
    kind: str
    rule: str

    def __post_init__(self) -> None:
        if not self.kind or not self.rule:
            raise ValueError("extra decisions require kind and rule")


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    destination_key: str
    candidates: tuple[str, ...]
    winner: str | None
    losers: tuple[str, ...]
    confidence: float
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.destination_key:
            raise ValueError("duplicate destination_key cannot be empty")
        if len(self.candidates) < 2:
            raise ValueError("duplicate decisions require at least two candidates")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("duplicate confidence must be between 0 and 1")
        if self.winner is not None and self.winner not in self.candidates:
            raise ValueError("duplicate winner must be one of the candidates")
        if any(loser not in self.candidates for loser in self.losers):
            raise ValueError("duplicate losers must be candidates")


@dataclass(frozen=True, slots=True)
class PlanEpisode:
    tvmaze_episode_id: int
    season: int
    number: int | None
    title: str
    airdate: str | None = None

    def __post_init__(self) -> None:
        if self.tvmaze_episode_id <= 0:
            raise ValueError("plan episode tvmaze_episode_id must be positive")
        if self.season < 0:
            raise ValueError("plan episode season cannot be negative")
        if self.number is not None and self.number < 0:
            raise ValueError("plan episode number cannot be negative")
        if not self.title:
            raise ValueError("plan episode title cannot be empty")
        if self.airdate is not None:
            try:
                normalized_date = date.fromisoformat(self.airdate).isoformat()
            except ValueError as exc:
                raise ValueError(
                    "plan episode airdate must use a valid YYYY-MM-DD date"
                ) from exc
            if normalized_date != self.airdate:
                raise ValueError("plan episode airdate must be canonical")


@dataclass(frozen=True, slots=True)
class CacheSnapshot:
    provider: str
    kind: str
    request_key: str
    snapshot_id: str
    state: str

    def __post_init__(self) -> None:
        if not all((self.provider, self.kind, self.request_key, self.state)):
            raise ValueError("cache snapshot identity fields cannot be empty")
        if len(self.snapshot_id) != 64:
            raise ValueError("cache snapshot_id must contain 64 hex characters")
        try:
            int(self.snapshot_id, 16)
        except ValueError as exc:
            raise ValueError(
                "cache snapshot_id must contain 64 hex characters"
            ) from exc


@dataclass(frozen=True, slots=True)
class PlanProvenance:
    tool_version: str
    config_snapshot_id: str
    overrides_snapshot_id: str
    cache_snapshots: tuple[CacheSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if not self.tool_version:
            raise ValueError("plan provenance tool_version cannot be empty")
        for field_name, value in (
            ("config_snapshot_id", self.config_snapshot_id),
            ("overrides_snapshot_id", self.overrides_snapshot_id),
        ):
            if len(value) != 64:
                raise ValueError(f"{field_name} must contain 64 hex characters")
            try:
                int(value, 16)
            except ValueError as exc:
                raise ValueError(
                    f"{field_name} must contain 64 hex characters"
                ) from exc


@dataclass(frozen=True, slots=True)
class CompanionPlanRecord:
    relative_path: str
    extension: str
    fingerprint: SourceFingerprint | None
    status: CompanionStatus
    reason: str
    source_video: str | None = None
    operation_group_id: str | None = None
    destination: str | None = None
    kind: str | None = None

    def __post_init__(self) -> None:
        if not self.relative_path:
            raise ValueError("companion relative_path cannot be empty")
        if not self.extension.startswith("."):
            raise ValueError("companion extension must start with '.'")
        if not self.reason:
            raise ValueError("companion reason cannot be empty")
        if self.status is CompanionStatus.ASSOCIATED:
            required = (
                self.fingerprint,
                self.source_video,
                self.operation_group_id,
                self.destination,
                self.kind,
            )
            if any(value is None for value in required):
                raise ValueError(
                    "associated companions require fingerprint, video, group, "
                    "destination, and kind"
                )


@dataclass(frozen=True, slots=True)
class PlanRecord:
    source: SourceFile
    status: TerminalStatus
    parse: ParseResult | None = None
    show: CanonicalShow | None = None
    evidence: MatchEvidence | None = None
    destination: str | None = None
    extra: ExtraDecision | None = None
    duplicate: DuplicateDecision | None = None
    operation_group_id: str | None = None
    provider_episodes: tuple[PlanEpisode, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status is TerminalStatus.MATCHED:
            required = (self.parse, self.show, self.evidence, self.destination)
            if any(value is None for value in required):
                raise ValueError(
                    "matched plan records require parse, show, evidence, and destination"
                )
        if self.status is TerminalStatus.EXTRA and self.extra is None:
            raise ValueError("extra plan records require an extra decision")
        if self.status is TerminalStatus.DUPLICATE and self.duplicate is None:
            raise ValueError("duplicate plan records require a duplicate decision")


@dataclass(frozen=True, slots=True)
class OrganizerPlan:
    schema_version: int
    overrides_version: int
    records: tuple[PlanRecord, ...]
    provenance: PlanProvenance | None = None
    companions: tuple[CompanionPlanRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version <= 0:
            raise ValueError("schema_version must be positive")
        if self.overrides_version <= 0:
            raise ValueError("overrides_version must be positive")
