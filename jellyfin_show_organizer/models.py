from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class NumberingMode(StrEnum):
    AIRED = "aired"
    ABSOLUTE = "absolute"
    PARENTHESIZED_ABSOLUTE = "parenthesized-absolute"
    SEGMENT_TITLE = "segment-title"


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


_PROVIDER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Namespaced identity for any metadata provider object."""

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
    season: int | None = None
    episodes: tuple[int, ...] = ()
    absolute_episode: int | None = None
    segment_hint: str | None = None
    year: int | None = None
    embedded_tvmaze_id: int | None = None
    title_hint: str | None = None
    embedded_provider_identity: ProviderIdentity | None = None

    def __post_init__(self) -> None:
        if self.season is not None and self.season < 0:
            raise ValueError("season cannot be negative")
        if any(episode < 0 for episode in self.episodes):
            raise ValueError("episodes cannot contain negative values")
        if self.absolute_episode is not None and self.absolute_episode < 0:
            raise ValueError("absolute_episode cannot be negative")
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
        if self.year is not None and self.year < 1800:
            raise ValueError("canonical show year is outside the supported range")

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

    def __post_init__(self) -> None:
        if self.schema_version <= 0:
            raise ValueError("schema_version must be positive")
        if self.overrides_version <= 0:
            raise ValueError("overrides_version must be positive")
