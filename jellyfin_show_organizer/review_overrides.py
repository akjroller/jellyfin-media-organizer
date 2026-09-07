from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from . import overrides as _base
from .destination import JellyfinProvider, JellyfinProviderIdentifier
from .models import ProviderIdentity
from .overrides import OverrideCatalog

REVIEW_OVERRIDE_SCHEMA_VERSION = 5
REVIEW_LOOKUP_MODES = frozenset({"coordinate", "absolute", "date", "special"})


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


def _validate_reasons(reasons: tuple[str, ...], label: str) -> None:
    if not reasons or any(not reason or reason != reason.strip() for reason in reasons):
        raise ValueError(f"{label} reasons must contain non-empty trimmed strings")
    normalized = [unicodedata.normalize("NFKC", reason).casefold() for reason in reasons]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} reasons must be unique")


@dataclass(frozen=True, slots=True)
class ReviewedEpisodeOverride:
    """One exact provider episode identity confirmed by a human review."""

    source: str
    source_binding_sha256: str
    show_provider_identity: ProviderIdentity
    episode_provider_identity: ProviderIdentity
    season: int
    number: int
    title: str
    airdate: str | None
    lookup_mode: str
    reasons: tuple[str, ...] = ("explicit reviewed provider episode",)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            _base._normalize_source_reference(
                self.source,
                label="reviewed episode source",
            ),
        )
        object.__setattr__(
            self,
            "source_binding_sha256",
            _sha256(self.source_binding_sha256, "reviewed episode source_binding_sha256"),
        )
        if self.show_provider_identity.provider != self.episode_provider_identity.provider:
            raise ValueError("reviewed episode and show must use the same provider")
        if self.season < 0 or self.number < 0:
            raise ValueError("reviewed episode coordinate cannot be negative")
        title = self.title.strip()
        if not title:
            raise ValueError("reviewed episode title must be non-empty")
        object.__setattr__(self, "title", title)
        mode = self.lookup_mode.strip().casefold()
        if mode not in REVIEW_LOOKUP_MODES:
            raise ValueError("reviewed episode lookup_mode is invalid")
        object.__setattr__(self, "lookup_mode", mode)
        if self.airdate is not None:
            airdate = self.airdate.strip()
            if not airdate:
                raise ValueError("reviewed episode airdate cannot be empty")
            object.__setattr__(self, "airdate", airdate)
        _validate_reasons(self.reasons, "reviewed episode")


@dataclass(frozen=True, slots=True)
class ExplicitExtraOverride:
    """One exact-source reviewed decision to classify a video as an extra."""

    source: str
    source_binding_sha256: str
    show_provider_identity: ProviderIdentity
    kind: str
    display_title: str | None = None
    reasons: tuple[str, ...] = ("explicit local extra decision",)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            _base._normalize_source_reference(
                self.source,
                label="extra decision source",
            ),
        )
        object.__setattr__(
            self,
            "source_binding_sha256",
            _sha256(self.source_binding_sha256, "extra decision source_binding_sha256"),
        )
        kind = self.kind.strip().casefold()
        if not kind:
            raise ValueError("extra decision kind must be non-empty")
        object.__setattr__(self, "kind", kind)
        if self.display_title is not None:
            title = self.display_title.strip()
            if not title:
                raise ValueError("extra decision display_title must be non-empty")
            object.__setattr__(self, "display_title", title)
        _validate_reasons(self.reasons, "extra decision")


@dataclass(frozen=True, slots=True)
class ShowJellyfinIdentifiers:
    """Reviewed Jellyfin-compatible identifiers attached to one override show key."""

    show_key: str
    identifiers: tuple[JellyfinProviderIdentifier, ...] = ()

    def __post_init__(self) -> None:
        if not self.show_key or self.show_key != self.show_key.strip():
            raise ValueError("show identifier key must be a non-empty trimmed string")
        providers = [identifier.provider for identifier in self.identifiers]
        if len(providers) != len(set(providers)):
            raise ValueError("show identifiers cannot repeat one Jellyfin provider")


@dataclass(frozen=True, slots=True)
class ReviewOverrideCatalog(OverrideCatalog):
    """Schema-5 structural extension used by the session-bound review contract."""

    reviewed_episode_decisions: tuple[ReviewedEpisodeOverride, ...] = ()
    extra_decisions: tuple[ExplicitExtraOverride, ...] = ()
    show_jellyfin_identifiers: tuple[ShowJellyfinIdentifiers, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != REVIEW_OVERRIDE_SCHEMA_VERSION:
            raise ValueError(
                f"review override catalog requires schema {REVIEW_OVERRIDE_SCHEMA_VERSION}"
            )
        _base.OverrideCatalog(
            schema_version=4,
            shows=self.shows,
            duplicate_preferences=self.duplicate_preferences,
            episode_decisions=self.episode_decisions,
            source_holds=self.source_holds,
        )

        hold_keys = {
            _base._source_reference_key(hold.source) for hold in self.source_holds
        }
        legacy_decision_keys = {
            _base._source_reference_key(decision.source)
            for decision in self.episode_decisions
        }
        reviewed_sources: dict[str, str] = {}
        for decision in self.reviewed_episode_decisions:
            normalized = _base._source_reference_key(decision.source)
            owner = reviewed_sources.get(normalized)
            if owner is not None:
                raise ValueError(
                    "reviewed episode source is configured more than once: "
                    f"{decision.source!r} conflicts with {owner!r}"
                )
            if normalized in hold_keys or normalized in legacy_decision_keys:
                raise ValueError(
                    "reviewed episode cannot overlap a source hold or legacy episode decision"
                )
            reviewed_sources[normalized] = decision.source

        extra_sources: dict[str, str] = {}
        for decision in self.extra_decisions:
            normalized = _base._source_reference_key(decision.source)
            owner = extra_sources.get(normalized)
            if owner is not None:
                raise ValueError(
                    "extra decision source is configured more than once: "
                    f"{decision.source!r} conflicts with {owner!r}"
                )
            if (
                normalized in hold_keys
                or normalized in legacy_decision_keys
                or normalized in reviewed_sources
            ):
                raise ValueError(
                    "extra decision cannot overlap another exact-source disposition"
                )
            extra_sources[normalized] = decision.source

        show_keys = {
            _base._normalize_identity(show.key): show.key for show in self.shows
        }
        identifier_keys: dict[str, str] = {}
        for metadata in self.show_jellyfin_identifiers:
            normalized = _base._normalize_identity(metadata.show_key)
            owner = identifier_keys.get(normalized)
            if owner is not None:
                raise ValueError(
                    "Jellyfin show identifiers are configured more than once: "
                    f"{metadata.show_key!r} conflicts with {owner!r}"
                )
            if normalized not in show_keys:
                raise ValueError(
                    "Jellyfin show identifiers must reference an existing show override"
                )
            identifier_keys[normalized] = metadata.show_key

    def reviewed_episode_for(
        self, source_relative_path: str
    ) -> ReviewedEpisodeOverride | None:
        normalized = _base._source_reference_key(source_relative_path)
        return next(
            (
                decision
                for decision in self.reviewed_episode_decisions
                if _base._source_reference_key(decision.source) == normalized
            ),
            None,
        )

    def extra_decision_for(
        self, source_relative_path: str
    ) -> ExplicitExtraOverride | None:
        normalized = _base._source_reference_key(source_relative_path)
        return next(
            (
                decision
                for decision in self.extra_decisions
                if _base._source_reference_key(decision.source) == normalized
            ),
            None,
        )

    def jellyfin_identifiers_for(
        self, show_key: str
    ) -> tuple[JellyfinProviderIdentifier, ...]:
        normalized = _base._normalize_identity(show_key)
        metadata = next(
            (
                item
                for item in self.show_jellyfin_identifiers
                if _base._normalize_identity(item.show_key) == normalized
            ),
            None,
        )
        return metadata.identifiers if metadata is not None else ()

    def canonical_bytes(self) -> bytes:
        base = _base.OverrideCatalog(
            schema_version=4,
            shows=self.shows,
            duplicate_preferences=self.duplicate_preferences,
            episode_decisions=self.episode_decisions,
            source_holds=self.source_holds,
        )
        payload = json.loads(base.canonical_bytes().decode("utf-8"))
        payload["schema_version"] = REVIEW_OVERRIDE_SCHEMA_VERSION
        payload["reviewed_episode_decisions"] = [
            {
                "airdate": decision.airdate,
                "episode_provider": decision.episode_provider_identity.provider,
                "episode_provider_id": decision.episode_provider_identity.value,
                "lookup_mode": decision.lookup_mode,
                "number": decision.number,
                "reasons": sorted(
                    decision.reasons,
                    key=lambda reason: (
                        unicodedata.normalize("NFKC", reason).casefold(),
                        reason,
                    ),
                ),
                "season": decision.season,
                "show_provider": decision.show_provider_identity.provider,
                "show_provider_id": decision.show_provider_identity.value,
                "source": decision.source,
                "source_binding_sha256": decision.source_binding_sha256,
                "title": decision.title,
            }
            for decision in sorted(
                self.reviewed_episode_decisions,
                key=lambda item: (
                    _base._source_reference_key(item.source),
                    item.source,
                ),
            )
        ]
        payload["extra_decisions"] = [
            {
                "display_title": decision.display_title,
                "kind": decision.kind,
                "reasons": sorted(
                    decision.reasons,
                    key=lambda reason: (
                        unicodedata.normalize("NFKC", reason).casefold(),
                        reason,
                    ),
                ),
                "show_provider": decision.show_provider_identity.provider,
                "show_provider_id": decision.show_provider_identity.value,
                "source": decision.source,
                "source_binding_sha256": decision.source_binding_sha256,
            }
            for decision in sorted(
                self.extra_decisions,
                key=lambda item: (
                    _base._source_reference_key(item.source),
                    item.source,
                ),
            )
        ]
        payload["show_jellyfin_identifiers"] = [
            {
                "identifiers": [
                    {
                        "provider": identifier.provider.value,
                        "value": identifier.value,
                    }
                    for identifier in sorted(
                        metadata.identifiers,
                        key=lambda item: (item.provider.value, item.value),
                    )
                ],
                "show_key": metadata.show_key,
            }
            for metadata in sorted(
                self.show_jellyfin_identifiers,
                key=lambda item: (
                    _base._normalize_identity(item.show_key),
                    item.show_key,
                ),
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


def _plain_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _identity(
    raw: dict[str, Any],
    *,
    provider_field: str,
    id_field: str,
    label: str,
) -> ProviderIdentity:
    provider = raw.get(provider_field)
    provider_id = raw.get(id_field)
    if not isinstance(provider, str):
        raise ValueError(f"{label} {provider_field} must be a string")
    if not (
        isinstance(provider_id, str)
        or (isinstance(provider_id, int) and not isinstance(provider_id, bool))
    ):
        raise ValueError(f"{label} {id_field} must be a string or integer")
    identity = ProviderIdentity(provider, str(provider_id))
    if identity.provider == "tvmaze":
        identity.require_positive_int("tvmaze")
    return identity


def _parse_reviewed_episode(raw: dict[str, Any]) -> ReviewedEpisodeOverride:
    allowed = {
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
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown reviewed episode fields: {sorted(unknown)}")
    source = raw.get("source")
    source_binding = raw.get("source_binding_sha256")
    title = raw.get("title")
    airdate = raw.get("airdate")
    lookup_mode = raw.get("lookup_mode")
    reasons = raw.get("reasons", ["explicit reviewed provider episode"])
    if not isinstance(source, str):
        raise ValueError("reviewed episode source must be a string")
    if not isinstance(source_binding, str):
        raise ValueError("reviewed episode source_binding_sha256 must be a string")
    if not isinstance(title, str):
        raise ValueError("reviewed episode title must be a string")
    if airdate is not None and not isinstance(airdate, str):
        raise ValueError("reviewed episode airdate must be a string")
    if not isinstance(lookup_mode, str):
        raise ValueError("reviewed episode lookup_mode must be a string")
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ValueError("reviewed episode reasons must be a list of strings")
    return ReviewedEpisodeOverride(
        source=source,
        source_binding_sha256=source_binding,
        show_provider_identity=_identity(
            raw,
            provider_field="show_provider",
            id_field="show_provider_id",
            label="reviewed episode",
        ),
        episode_provider_identity=_identity(
            raw,
            provider_field="episode_provider",
            id_field="episode_provider_id",
            label="reviewed episode",
        ),
        season=_plain_int(raw.get("season"), "reviewed episode season"),
        number=_plain_int(raw.get("number"), "reviewed episode number"),
        title=title,
        airdate=airdate,
        lookup_mode=lookup_mode,
        reasons=tuple(reasons),
    )


def _parse_extra_decision(raw: dict[str, Any]) -> ExplicitExtraOverride:
    allowed = {
        "source",
        "source_binding_sha256",
        "show_provider",
        "show_provider_id",
        "kind",
        "display_title",
        "reasons",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown extra decision fields: {sorted(unknown)}")
    source = raw.get("source")
    source_binding = raw.get("source_binding_sha256")
    kind = raw.get("kind")
    display_title = raw.get("display_title")
    reasons = raw.get("reasons", ["explicit local extra decision"])
    if not isinstance(source, str):
        raise ValueError("extra decision source must be a string")
    if not isinstance(source_binding, str):
        raise ValueError("extra decision source_binding_sha256 must be a string")
    if not isinstance(kind, str):
        raise ValueError("extra decision kind must be a string")
    if display_title is not None and not isinstance(display_title, str):
        raise ValueError("extra decision display_title must be a string")
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ValueError("extra decision reasons must be a list of strings")
    return ExplicitExtraOverride(
        source=source,
        source_binding_sha256=source_binding,
        show_provider_identity=_identity(
            raw,
            provider_field="show_provider",
            id_field="show_provider_id",
            label="extra decision",
        ),
        kind=kind,
        display_title=display_title,
        reasons=tuple(reasons),
    )


def _show_ids(raw: dict[str, Any]) -> tuple[JellyfinProviderIdentifier, ...]:
    values: list[JellyfinProviderIdentifier] = []
    for field, provider in (
        ("tmdb_id", JellyfinProvider.TMDB),
        ("tvdb_id", JellyfinProvider.TVDB),
        ("imdb_id", JellyfinProvider.IMDB),
    ):
        value = raw.get(field)
        if value is None:
            continue
        if not (
            isinstance(value, str)
            or (isinstance(value, int) and not isinstance(value, bool))
        ):
            raise ValueError(f"show {field} must be a string or integer")
        values.append(JellyfinProviderIdentifier(provider, str(value)))
    return tuple(values)
