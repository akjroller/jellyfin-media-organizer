from __future__ import annotations

import hashlib
import json
import re
import tomllib
import unicodedata
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any, TypeGuard

from .models import NumberingMode, ParseResult, ProviderIdentity, TitlePreference

OVERRIDES_RESOURCE = "data/overrides-v1.toml"
SUPPORTED_OVERRIDE_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4})


def _normalize_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _normalize_source_reference(
    value: str,
    *,
    label: str = "local override source",
) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed path")
    normalized = unicodedata.normalize("NFC", value.replace("\\", "/"))
    if re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"{label} must be relative")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts:
        raise ValueError(f"{label} must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} cannot contain dot segments")
    return path.as_posix()


def _source_reference_key(value: str) -> str:
    return unicodedata.normalize(
        "NFKC",
        _normalize_source_reference(value),
    ).casefold()


def _is_plain_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True, init=False)
class ShowOverride:
    key: str
    provider_identity: ProviderIdentity | None
    aliases: tuple[str, ...]
    year: int | None
    numbering_mode: NumberingMode
    title_preference: TitlePreference
    preferred_title: str | None

    def __init__(
        self,
        key: str,
        tvmaze_id: int | None = None,
        aliases: tuple[str, ...] = (),
        year: int | None = None,
        numbering_mode: NumberingMode = NumberingMode.AIRED,
        title_preference: TitlePreference = TitlePreference.PROVIDER,
        preferred_title: str | None = None,
        *,
        provider_identity: ProviderIdentity | None = None,
    ) -> None:
        if provider_identity is None and tvmaze_id is not None:
            provider_identity = ProviderIdentity.tvmaze(tvmaze_id)
        elif provider_identity is not None and tvmaze_id is not None:
            legacy_identity = ProviderIdentity.tvmaze(tvmaze_id)
            if provider_identity != legacy_identity:
                raise ValueError("conflicting override provider identities")

        object.__setattr__(self, "key", key)
        object.__setattr__(self, "provider_identity", provider_identity)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "year", year)
        object.__setattr__(self, "numbering_mode", numbering_mode)
        object.__setattr__(self, "title_preference", title_preference)
        object.__setattr__(self, "preferred_title", preferred_title)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.key or self.key != self.key.strip():
            raise ValueError("override key must be a non-empty trimmed string")
        if self.year is not None and not 1800 <= self.year <= 9999:
            raise ValueError("override year is outside the supported range")

        normalized_aliases: set[str] = set()
        for alias in self.aliases:
            if not alias or alias != alias.strip():
                raise ValueError(
                    "override aliases must contain non-empty trimmed strings"
                )
            normalized = _normalize_identity(alias)
            if normalized in normalized_aliases:
                raise ValueError("override aliases must be unique after normalization")
            normalized_aliases.add(normalized)

        if self.preferred_title is not None:
            if (
                not self.preferred_title
                or self.preferred_title != self.preferred_title.strip()
            ):
                raise ValueError(
                    "override preferred_title must be a non-empty trimmed string"
                )
        if (
            self.title_preference is TitlePreference.OVERRIDE
            and self.preferred_title is None
        ):
            raise ValueError("title_preference='override' requires preferred_title")

    @property
    def provider(self) -> str | None:
        if self.provider_identity is None:
            return None
        return self.provider_identity.provider

    @property
    def provider_id(self) -> str | None:
        if self.provider_identity is None:
            return None
        return self.provider_identity.value

    @property
    def tvmaze_id(self) -> int | None:
        """Compatibility alias for existing TVMaze override files and callers."""

        if self.provider_identity is None:
            return None
        if self.provider_identity.provider != "tvmaze":
            return None
        return self.provider_identity.require_positive_int("tvmaze")


@dataclass(frozen=True, slots=True)
class DuplicatePreferenceOverride:
    """One explicit local preference for a source participating in a collision."""

    source: str
    rank: int
    reasons: tuple[str, ...] = ("explicit local duplicate preference",)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            _normalize_source_reference(
                self.source,
                label="duplicate preference source",
            ),
        )
        if self.rank < 0:
            raise ValueError("duplicate preference rank cannot be negative")
        if not self.reasons or any(
            not reason or reason != reason.strip() for reason in self.reasons
        ):
            raise ValueError(
                "duplicate preference reasons must contain non-empty trimmed strings"
            )

        normalized_reasons = [
            unicodedata.normalize("NFKC", reason).casefold() for reason in self.reasons
        ]
        if len(normalized_reasons) != len(set(normalized_reasons)):
            raise ValueError("duplicate preference reasons must be unique")


@dataclass(frozen=True, slots=True)
class SourceHoldOverride:
    """One exact-source local decision to leave a video untouched."""

    source: str
    reasons: tuple[str, ...] = ("explicit local leave-in-place decision",)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            _normalize_source_reference(self.source, label="source hold source"),
        )
        if not self.reasons or any(
            not reason or reason != reason.strip() for reason in self.reasons
        ):
            raise ValueError(
                "source hold reasons must contain non-empty trimmed strings"
            )
        normalized_reasons = [
            unicodedata.normalize("NFKC", reason).casefold() for reason in self.reasons
        ]
        if len(normalized_reasons) != len(set(normalized_reasons)):
            raise ValueError("source hold reasons must be unique")


def _decision_family(parse: ParseResult) -> str:
    families = [
        family
        for family, present in (
            ("aired", parse.season is not None or bool(parse.episodes)),
            ("absolute", parse.absolute_episode is not None),
            (
                "special",
                parse.special_kind is not None or parse.special_episode is not None,
            ),
            ("date", parse.episode_date is not None),
            ("segment", parse.segment_hint is not None),
        )
        if present
    ]
    if len(families) != 1:
        return "conflict" if families else "none"
    return families[0]


def _expected_decision_family(mode: NumberingMode) -> str:
    if mode is NumberingMode.AIRED:
        return "aired"
    if mode in {NumberingMode.ABSOLUTE, NumberingMode.PARENTHESIZED_ABSOLUTE}:
        return "absolute"
    if mode is NumberingMode.SPECIAL:
        return "special"
    if mode is NumberingMode.DATE:
        return "date"
    return "segment"


@dataclass(frozen=True, slots=True)
class EpisodeDecisionOverride:
    """One exact-source local decision for episode numbering evidence."""

    source: str
    show_provider_identity: ProviderIdentity
    numbering_mode: NumberingMode
    parse: ParseResult
    reasons: tuple[str, ...] = ("explicit local episode decision",)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            _normalize_source_reference(
                self.source,
                label="episode decision source",
            ),
        )
        expected = _expected_decision_family(self.numbering_mode)
        family = _decision_family(self.parse)
        if family != expected:
            raise ValueError(
                "episode decision numbering evidence does not match "
                f"numbering_mode={self.numbering_mode.value!r}"
            )

        if self.numbering_mode is NumberingMode.AIRED:
            if self.parse.season is None or not self.parse.episodes:
                raise ValueError(
                    "aired episode decisions require season and at least one episode"
                )
            if len(set(self.parse.episodes)) != len(self.parse.episodes):
                raise ValueError("episode decision episodes must be unique")
        elif self.numbering_mode in {
            NumberingMode.ABSOLUTE,
            NumberingMode.PARENTHESIZED_ABSOLUTE,
        }:
            if self.parse.absolute_episode is None or self.parse.absolute_episode <= 0:
                raise ValueError(
                    "absolute episode decisions require a positive absolute_episode"
                )
        elif self.numbering_mode is NumberingMode.SEGMENT_TITLE:
            if (
                self.parse.segment_hint is None
                or not self.parse.segment_hint.strip()
                or self.parse.segment_hint != self.parse.segment_hint.strip()
                or self.parse.title_hint is None
                or not self.parse.title_hint.strip()
                or self.parse.title_hint != self.parse.title_hint.strip()
            ):
                raise ValueError(
                    "segment-title episode decisions require trimmed "
                    "segment_hint and title_hint"
                )

        if (
            self.numbering_mode is not NumberingMode.SEGMENT_TITLE
            and self.parse.title_hint is not None
        ):
            raise ValueError(
                "episode decision title_hint is only valid for segment-title mode"
            )

        if not self.reasons or any(
            not reason or reason != reason.strip() for reason in self.reasons
        ):
            raise ValueError(
                "episode decision reasons must contain non-empty trimmed strings"
            )
        normalized_reasons = [
            unicodedata.normalize("NFKC", reason).casefold() for reason in self.reasons
        ]
        if len(normalized_reasons) != len(set(normalized_reasons)):
            raise ValueError("episode decision reasons must be unique")

    @property
    def show_provider(self) -> str:
        return self.show_provider_identity.provider

    @property
    def show_provider_id(self) -> str:
        return self.show_provider_identity.value

    def apply_to(self, parse: ParseResult) -> ParseResult:
        """Replace only episode-numbering evidence while preserving show evidence."""

        title_hint = (
            self.parse.title_hint
            if self.numbering_mode is NumberingMode.SEGMENT_TITLE
            else parse.title_hint
        )
        return replace(
            parse,
            season=self.parse.season,
            episodes=self.parse.episodes,
            absolute_episode=self.parse.absolute_episode,
            special_kind=self.parse.special_kind,
            special_episode=self.parse.special_episode,
            episode_date=self.parse.episode_date,
            segment_hint=self.parse.segment_hint,
            title_hint=title_hint,
        )


@dataclass(frozen=True, slots=True)
class OverrideCatalog:
    schema_version: int
    shows: tuple[ShowOverride, ...]
    duplicate_preferences: tuple[DuplicatePreferenceOverride, ...] = ()
    episode_decisions: tuple[EpisodeDecisionOverride, ...] = ()
    source_holds: tuple[SourceHoldOverride, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_OVERRIDE_SCHEMA_VERSIONS:
            supported = ", ".join(
                str(version) for version in sorted(SUPPORTED_OVERRIDE_SCHEMA_VERSIONS)
            )
            raise ValueError(
                "unsupported override schema_version: "
                f"{self.schema_version}; supported versions: {supported}"
            )
        if self.schema_version < 2 and self.duplicate_preferences:
            raise ValueError("duplicate preferences require override schema_version 2")
        if self.schema_version < 3 and self.episode_decisions:
            raise ValueError("episode decisions require override schema_version 3")
        if self.schema_version < 4 and self.source_holds:
            raise ValueError("source holds require override schema_version 4")

        identities: dict[str, str] = {}
        provider_ids: dict[ProviderIdentity, str] = {}
        for show in self.shows:
            values = [show.key, *show.aliases]
            if show.preferred_title is not None:
                values.append(show.preferred_title)
            for value in values:
                normalized = _normalize_identity(value)
                owner = identities.get(normalized)
                if owner is not None and owner != show.key:
                    raise ValueError(
                        "override identity is ambiguous after normalization: "
                        f"{value!r} conflicts with {owner!r}"
                    )
                identities[normalized] = show.key

            if show.provider_identity is not None:
                owner = provider_ids.get(show.provider_identity)
                if owner is not None and owner != show.key:
                    raise ValueError(
                        "override provider identity is assigned to multiple entries: "
                        f"{show.provider_identity.key}"
                    )
                provider_ids[show.provider_identity] = show.key

        duplicate_sources: dict[str, str] = {}
        for preference in self.duplicate_preferences:
            normalized = _source_reference_key(preference.source)
            owner = duplicate_sources.get(normalized)
            if owner is not None:
                raise ValueError(
                    "duplicate preference source is configured more than once: "
                    f"{preference.source!r} conflicts with {owner!r}"
                )
            duplicate_sources[normalized] = preference.source

        decision_sources: dict[str, str] = {}
        for decision in self.episode_decisions:
            normalized = _source_reference_key(decision.source)
            owner = decision_sources.get(normalized)
            if owner is not None:
                raise ValueError(
                    "episode decision source is configured more than once: "
                    f"{decision.source!r} conflicts with {owner!r}"
                )
            decision_sources[normalized] = decision.source

        hold_sources: dict[str, str] = {}
        for hold in self.source_holds:
            normalized = _source_reference_key(hold.source)
            owner = hold_sources.get(normalized)
            if owner is not None:
                raise ValueError(
                    "source hold is configured more than once: "
                    f"{hold.source!r} conflicts with {owner!r}"
                )
            if normalized in duplicate_sources or normalized in decision_sources:
                raise ValueError(
                    "source hold cannot overlap an episode decision or duplicate preference"
                )
            hold_sources[normalized] = hold.source

    def get(self, key: str) -> ShowOverride | None:
        normalized = _normalize_identity(key)
        return next(
            (
                show
                for show in self.shows
                if _normalize_identity(show.key) == normalized
            ),
            None,
        )

    def duplicate_preference_for(
        self, source_relative_path: str
    ) -> DuplicatePreferenceOverride | None:
        normalized = _source_reference_key(source_relative_path)
        return next(
            (
                preference
                for preference in self.duplicate_preferences
                if _source_reference_key(preference.source) == normalized
            ),
            None,
        )

    def episode_decision_for(
        self, source_relative_path: str
    ) -> EpisodeDecisionOverride | None:
        normalized = _source_reference_key(source_relative_path)
        return next(
            (
                decision
                for decision in self.episode_decisions
                if _source_reference_key(decision.source) == normalized
            ),
            None,
        )

    def source_hold_for(self, source_relative_path: str) -> SourceHoldOverride | None:
        normalized = _source_reference_key(source_relative_path)
        return next(
            (
                hold
                for hold in self.source_holds
                if _source_reference_key(hold.source) == normalized
            ),
            None,
        )

    def canonical_bytes(self) -> bytes:
        """Return a path-independent deterministic representation for audit hashing."""

        canonical_shows = []
        for show in sorted(
            self.shows,
            key=lambda item: (_normalize_identity(item.key), item.key),
        ):
            canonical_shows.append(
                {
                    "aliases": sorted(
                        show.aliases,
                        key=lambda alias: (_normalize_identity(alias), alias),
                    ),
                    "key": show.key,
                    "numbering_mode": show.numbering_mode.value,
                    "preferred_title": show.preferred_title,
                    "provider": show.provider,
                    "provider_id": show.provider_id,
                    "title_preference": show.title_preference.value,
                    "year": show.year,
                }
            )

        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "shows": canonical_shows,
        }
        if self.schema_version >= 2:
            payload["duplicate_preferences"] = [
                {
                    "rank": preference.rank,
                    "reasons": sorted(
                        preference.reasons,
                        key=lambda reason: (
                            unicodedata.normalize("NFKC", reason).casefold(),
                            reason,
                        ),
                    ),
                    "source": preference.source,
                }
                for preference in sorted(
                    self.duplicate_preferences,
                    key=lambda item: (_source_reference_key(item.source), item.source),
                )
            ]
        if self.schema_version >= 3:
            payload["episode_decisions"] = [
                {
                    "absolute_episode": decision.parse.absolute_episode,
                    "episode_date": decision.parse.episode_date,
                    "episodes": list(decision.parse.episodes),
                    "numbering_mode": decision.numbering_mode.value,
                    "reasons": sorted(
                        decision.reasons,
                        key=lambda reason: (
                            unicodedata.normalize("NFKC", reason).casefold(),
                            reason,
                        ),
                    ),
                    "season": decision.parse.season,
                    "segment_hint": decision.parse.segment_hint,
                    "show_provider": decision.show_provider,
                    "show_provider_id": decision.show_provider_id,
                    "source": decision.source,
                    "special_episode": decision.parse.special_episode,
                    "special_kind": decision.parse.special_kind,
                    "title_hint": decision.parse.title_hint,
                }
                for decision in sorted(
                    self.episode_decisions,
                    key=lambda item: (_source_reference_key(item.source), item.source),
                )
            ]
        if self.schema_version >= 4:
            payload["source_holds"] = [
                {
                    "reasons": sorted(
                        hold.reasons,
                        key=lambda reason: (
                            unicodedata.normalize("NFKC", reason).casefold(),
                            reason,
                        ),
                    ),
                    "source": hold.source,
                }
                for hold in sorted(
                    self.source_holds,
                    key=lambda item: (_source_reference_key(item.source), item.source),
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
        """Return a stable SHA-256 identity without exposing the local file path."""

        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _read_default_overrides() -> bytes:
    resource = files("jellyfin_show_organizer").joinpath(OVERRIDES_RESOURCE)
    return resource.read_bytes()


def _provider_identity(raw: dict[str, Any]) -> ProviderIdentity | None:
    tvmaze_id = raw.get("tvmaze_id")
    provider = raw.get("provider")
    provider_id = raw.get("provider_id")

    if tvmaze_id is not None and not _is_plain_int(tvmaze_id):
        raise ValueError("override tvmaze_id must be an integer")
    if provider is not None and not isinstance(provider, str):
        raise ValueError("override provider must be a string")
    if provider_id is not None and not (
        isinstance(provider_id, str) or _is_plain_int(provider_id)
    ):
        raise ValueError("override provider_id must be a string or integer")
    if (provider is None) != (provider_id is None):
        raise ValueError("override provider and provider_id must be supplied together")

    legacy = ProviderIdentity.tvmaze(tvmaze_id) if tvmaze_id is not None else None
    generic = (
        ProviderIdentity(provider, str(provider_id))
        if provider is not None and provider_id is not None
        else None
    )
    if legacy is not None and generic is not None and legacy != generic:
        raise ValueError("conflicting override provider identities")
    return generic or legacy


def _parse_override(raw: dict[str, Any]) -> ShowOverride:
    allowed = {
        "key",
        "tvmaze_id",
        "provider",
        "provider_id",
        "aliases",
        "year",
        "numbering_mode",
        "title_preference",
        "preferred_title",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown override fields: {sorted(unknown)}")

    key = raw.get("key")
    aliases = raw.get("aliases", [])
    if not isinstance(key, str):
        raise ValueError("override key must be a string")
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) for alias in aliases
    ):
        raise ValueError("override aliases must be a list of strings")

    year = raw.get("year")
    preferred_title = raw.get("preferred_title")
    if year is not None and not _is_plain_int(year):
        raise ValueError("override year must be an integer")
    if preferred_title is not None and not isinstance(preferred_title, str):
        raise ValueError("override preferred_title must be a string")

    try:
        numbering_mode = NumberingMode(raw.get("numbering_mode", "aired"))
    except ValueError as exc:
        raise ValueError("invalid override numbering_mode") from exc
    try:
        title_preference = TitlePreference(raw.get("title_preference", "provider"))
    except ValueError as exc:
        raise ValueError("invalid override title_preference") from exc

    return ShowOverride(
        key=key,
        provider_identity=_provider_identity(raw),
        aliases=tuple(aliases),
        year=year,
        numbering_mode=numbering_mode,
        title_preference=title_preference,
        preferred_title=preferred_title,
    )


def _parse_duplicate_preference(raw: dict[str, Any]) -> DuplicatePreferenceOverride:
    allowed = {"source", "rank", "reasons"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown duplicate preference fields: {sorted(unknown)}")

    source = raw.get("source")
    rank = raw.get("rank")
    reasons = raw.get("reasons", ["explicit local duplicate preference"])
    if not isinstance(source, str):
        raise ValueError("duplicate preference source must be a string")
    if not _is_plain_int(rank):
        raise ValueError("duplicate preference rank must be an integer")
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ValueError("duplicate preference reasons must be a list of strings")

    return DuplicatePreferenceOverride(
        source=source,
        rank=rank,
        reasons=tuple(reasons),
    )


def _episode_decision_identity(raw: dict[str, Any]) -> ProviderIdentity:
    provider = raw.get("show_provider")
    provider_id = raw.get("show_provider_id")
    if not isinstance(provider, str):
        raise ValueError("episode decision show_provider must be a string")
    if not (isinstance(provider_id, str) or _is_plain_int(provider_id)):
        raise ValueError(
            "episode decision show_provider_id must be a string or integer"
        )
    identity = ProviderIdentity(provider, str(provider_id))
    if identity.provider == "tvmaze":
        identity.require_positive_int("tvmaze")
    return identity


def _optional_int(raw: dict[str, Any], field: str) -> int | None:
    value = raw.get(field)
    if value is not None and not _is_plain_int(value):
        raise ValueError(f"episode decision {field} must be an integer")
    return value


def _optional_string(raw: dict[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"episode decision {field} must be a string")
    return value


def _parse_episode_decision(raw: dict[str, Any]) -> EpisodeDecisionOverride:
    allowed = {
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
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown episode decision fields: {sorted(unknown)}")

    source = raw.get("source")
    if not isinstance(source, str):
        raise ValueError("episode decision source must be a string")

    raw_episodes = raw.get("episodes", [])
    if not isinstance(raw_episodes, list) or not all(
        _is_plain_int(episode) for episode in raw_episodes
    ):
        raise ValueError("episode decision episodes must be a list of integers")

    reasons = raw.get("reasons", ["explicit local episode decision"])
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ValueError("episode decision reasons must be a list of strings")

    raw_numbering_mode = raw.get("numbering_mode")
    if not isinstance(raw_numbering_mode, str):
        raise ValueError("episode decision numbering_mode must be a string")
    try:
        numbering_mode = NumberingMode(raw_numbering_mode)
    except ValueError as exc:
        raise ValueError("invalid episode decision numbering_mode") from exc

    parse = ParseResult(
        season=_optional_int(raw, "season"),
        episodes=tuple(raw_episodes),
        absolute_episode=_optional_int(raw, "absolute_episode"),
        special_kind=_optional_string(raw, "special_kind"),
        special_episode=_optional_int(raw, "special_episode"),
        episode_date=_optional_string(raw, "episode_date"),
        segment_hint=_optional_string(raw, "segment_hint"),
        title_hint=_optional_string(raw, "title_hint"),
    )
    return EpisodeDecisionOverride(
        source=source,
        show_provider_identity=_episode_decision_identity(raw),
        numbering_mode=numbering_mode,
        parse=parse,
        reasons=tuple(reasons),
    )


def _parse_source_hold(raw: dict[str, Any]) -> SourceHoldOverride:
    allowed = {"source", "reasons"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown source hold fields: {sorted(unknown)}")
    source = raw.get("source")
    reasons = raw.get("reasons", ["explicit local leave-in-place decision"])
    if not isinstance(source, str):
        raise ValueError("source hold source must be a string")
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ValueError("source hold reasons must be a list of strings")
    return SourceHoldOverride(source=source, reasons=tuple(reasons))


def load_overrides(path: Path | None = None) -> OverrideCatalog:
    payload = path.read_bytes() if path is not None else _read_default_overrides()
    try:
        raw = tomllib.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("override file must be valid UTF-8") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid override TOML: {exc}") from exc

    allowed_top_level = {
        "schema_version",
        "shows",
        "duplicate_preferences",
        "episode_decisions",
        "source_holds",
    }
    unknown_top_level = set(raw) - allowed_top_level
    if unknown_top_level:
        raise ValueError(
            f"unknown top-level override fields: {sorted(unknown_top_level)}"
        )

    schema_version = raw.get("schema_version")
    shows = raw.get("shows", [])
    duplicate_preferences = raw.get("duplicate_preferences", [])
    episode_decisions = raw.get("episode_decisions", [])
    source_holds = raw.get("source_holds", [])
    if not _is_plain_int(schema_version):
        raise ValueError("override schema_version must be an integer")
    if not isinstance(shows, list) or not all(isinstance(show, dict) for show in shows):
        raise ValueError("override shows must be an array of tables")
    if not isinstance(duplicate_preferences, list) or not all(
        isinstance(preference, dict) for preference in duplicate_preferences
    ):
        raise ValueError("duplicate_preferences must be an array of tables")
    if not isinstance(episode_decisions, list) or not all(
        isinstance(decision, dict) for decision in episode_decisions
    ):
        raise ValueError("episode_decisions must be an array of tables")
    if schema_version < 3 and "episode_decisions" in raw:
        raise ValueError("episode decisions require override schema_version 3")
    if not isinstance(source_holds, list) or not all(
        isinstance(hold, dict) for hold in source_holds
    ):
        raise ValueError("source_holds must be an array of tables")
    if schema_version < 4 and "source_holds" in raw:
        raise ValueError("source holds require override schema_version 4")

    return OverrideCatalog(
        schema_version=schema_version,
        shows=tuple(_parse_override(show) for show in shows),
        duplicate_preferences=tuple(
            _parse_duplicate_preference(preference)
            for preference in duplicate_preferences
        ),
        episode_decisions=tuple(
            _parse_episode_decision(decision) for decision in episode_decisions
        ),
        source_holds=tuple(_parse_source_hold(hold) for hold in source_holds),
    )
