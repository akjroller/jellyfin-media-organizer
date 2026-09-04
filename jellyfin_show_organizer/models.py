from __future__ import annotations

import re
import unicodedata
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
    HELD = "held"
    SUSPICIOUS = "suspicious"
    UNRESOLVED = "unresolved"


class CompanionStatus(StrEnum):
    ASSOCIATED = "associated"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    UNRESOLVED = "unresolved"


_PROVIDER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Namespaced identity for one metadata-provider object."""

    provider: str
    value: str

    def __post_init__(self) -> None:
        provider = self.normalize_provider(self.provider)
        value = unicodedata.normalize("NFKC", str(self.value)).strip()
        if not provider or _PROVIDER_NAME.fullmatch(provider) is None:
            raise ValueError("provider name must be a normalized identifier")
        if not value:
            raise ValueError("provider identity value cannot be empty")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "value", value)

    @staticmethod
    def normalize_provider(provider: str) -> str:
        return unicodedata.normalize("NFKC", provider).strip().casefold()

    @classmethod
    def tvmaze(cls, value: int) -> ProviderIdentity:
        if value <= 0:
            raise ValueError("TVMaze identity must be positive")
        return cls("tvmaze", str(value))

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.value}"

    def require_positive_int(self, provider: str) -> int:
        expected = self.normalize_provider(provider)
        if self.provider != expected:
            raise ValueError(
                f"provider identity is {self.provider!r}, expected {expected!r}"
            )
        try:
            value = int(self.value)
        except ValueError as exc:
            raise ValueError("provider identity must be a positive integer") from exc
        if value <= 0 or str(value) != self.value:
            raise ValueError("provider identity must be a canonical positive integer")
        return value


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
    series_aliases: tuple[str, ...] = ()
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
    embedded_provider_identity: ProviderIdentity | None = None

    def __post_init__(self) -> None:
        if len(self.series_aliases) > 2:
            raise ValueError("series_aliases can contain at most two titles")
        normalized_aliases: set[str] = set()
        for alias in self.series_aliases:
            if not alias or alias != alias.strip():
                raise ValueError("series_aliases must contain non-empty trimmed titles")
            normalized = unicodedata.normalize("NFKC", alias).casefold()
            normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
            normalized = " ".join(normalized.split())
            if not normalized or normalized in normalized_aliases:
                raise ValueError("series_aliases must be unique after normalization")
            normalized_aliases.add(normalized)
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
        if (
            self.embedded_tvmaze_id is not None
            and self.embedded_provider_identity is not None
            and self.embedded_provider_identity
            != ProviderIdentity.tvmaze(self.embedded_tvmaze_id)
        ):
            raise ValueError("conflicting embedded provider identities")

    @property
    def provider_identities(self) -> tuple[ProviderIdentity, ...]:
        identities: set[ProviderIdentity] = set()
        if self.embedded_tvmaze_id is not None:
            identities.add(ProviderIdentity.tvmaze(self.embedded_tvmaze_id))
        if self.embedded_provider_identity is not None:
            identities.add(self.embedded_provider_identity)
        return tuple(sorted(identities, key=lambda identity: identity.key))


@dataclass(frozen=True, slots=True, init=False)
class CanonicalShow:
    source_key: str
    provider_identity: ProviderIdentity
    title: str
    year: int | None
    numbering_mode: NumberingMode

    def __init__(
        self,
        source_key: str,
        tvmaze_id: int | None = None,
        title: str | None = None,
        year: int | None = None,
        numbering_mode: NumberingMode = NumberingMode.AIRED,
        *,
        provider_identity: ProviderIdentity | None = None,
    ) -> None:
        if provider_identity is None:
            if tvmaze_id is None:
                raise ValueError("canonical show provider identity is required")
            provider_identity = ProviderIdentity.tvmaze(tvmaze_id)
        elif tvmaze_id is not None:
            legacy_identity = ProviderIdentity.tvmaze(tvmaze_id)
            if provider_identity != legacy_identity:
                raise ValueError("conflicting canonical show provider identities")

        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "provider_identity", provider_identity)
        object.__setattr__(self, "title", "" if title is None else title)
        object.__setattr__(self, "year", year)
        object.__setattr__(self, "numbering_mode", numbering_mode)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.source_key:
            raise ValueError("canonical show source_key cannot be empty")
        if not self.title:
            raise ValueError("canonical show title cannot be empty")

    @property
    def provider(self) -> str:
        return self.provider_identity.provider

    @property
    def provider_id(self) -> str:
        return self.provider_identity.value

    @property
    def tvmaze_id(self) -> int:
        """Compatibility alias for callers still using the initial provider."""

        return self.provider_identity.require_positive_int("tvmaze")


@dataclass(frozen=True, slots=True, init=False)
class CandidateEvidence:
    provider_identity: ProviderIdentity
    title: str
    score: float
    reasons: tuple[str, ...]

    def __init__(
        self,
        tvmaze_id: int | None = None,
        title: str | None = None,
        score: float = 0.0,
        reasons: tuple[str, ...] = (),
        *,
        provider_identity: ProviderIdentity | None = None,
    ) -> None:
        if provider_identity is None:
            if tvmaze_id is None:
                raise ValueError("candidate provider identity is required")
            provider_identity = ProviderIdentity.tvmaze(tvmaze_id)
        elif tvmaze_id is not None:
            legacy_identity = ProviderIdentity.tvmaze(tvmaze_id)
            if provider_identity != legacy_identity:
                raise ValueError("conflicting candidate provider identities")

        object.__setattr__(self, "provider_identity", provider_identity)
        object.__setattr__(self, "title", "" if title is None else title)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "reasons", reasons)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("candidate title cannot be empty")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("candidate score must be between 0 and 1")

    @property
    def provider(self) -> str:
        return self.provider_identity.provider

    @property
    def provider_id(self) -> str:
        return self.provider_identity.value

    @property
    def tvmaze_id(self) -> int:
        return self.provider_identity.require_positive_int("tvmaze")


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


@dataclass(frozen=True, slots=True, init=False)
class PlanEpisode:
    provider_identity: ProviderIdentity
    season: int
    number: int | None
    title: str
    airdate: str | None

    def __init__(
        self,
        tvmaze_episode_id: int | None = None,
        season: int = 0,
        number: int | None = None,
        title: str = "",
        airdate: str | None = None,
        *,
        provider_identity: ProviderIdentity | None = None,
    ) -> None:
        if provider_identity is None:
            if tvmaze_episode_id is None:
                raise ValueError("plan episode provider identity is required")
            provider_identity = ProviderIdentity.tvmaze(tvmaze_episode_id)
        elif tvmaze_episode_id is not None:
            legacy_identity = ProviderIdentity.tvmaze(tvmaze_episode_id)
            if provider_identity != legacy_identity:
                raise ValueError("conflicting plan episode provider identities")

        object.__setattr__(self, "provider_identity", provider_identity)
        object.__setattr__(self, "season", season)
        object.__setattr__(self, "number", number)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "airdate", airdate)
        self.__post_init__()

    def __post_init__(self) -> None:
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

    @property
    def provider(self) -> str:
        return self.provider_identity.provider

    @property
    def provider_id(self) -> str:
        return self.provider_identity.value

    @property
    def tvmaze_episode_id(self) -> int:
        return self.provider_identity.require_positive_int("tvmaze")


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
        if self.status is TerminalStatus.HELD:
            if (
                self.destination is not None
                or self.extra is not None
                or self.duplicate is not None
            ):
                raise ValueError(
                    "held plan records must be non-moving and non-duplicate"
                )
            if self.provider_episodes:
                raise ValueError("held plan records cannot carry provider episodes")
            if self.evidence is None or self.reason is None:
                raise ValueError(
                    "held plan records require audit evidence and a reason"
                )


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
