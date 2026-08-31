from __future__ import annotations

import hashlib
import json
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .models import NumberingMode, TitlePreference

OVERRIDES_RESOURCE = "data/overrides-v1.toml"
SUPPORTED_OVERRIDE_SCHEMA_VERSION = 1


def _normalize_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class ShowOverride:
    key: str
    tvmaze_id: int | None
    aliases: tuple[str, ...]
    year: int | None
    numbering_mode: NumberingMode
    title_preference: TitlePreference
    preferred_title: str | None = None

    def __post_init__(self) -> None:
        if not self.key or self.key != self.key.strip():
            raise ValueError("override key must be a non-empty trimmed string")
        if self.tvmaze_id is not None and self.tvmaze_id <= 0:
            raise ValueError("override tvmaze_id must be positive")
        if self.year is not None and not 1800 <= self.year <= 9999:
            raise ValueError("override year is outside the supported range")

        normalized_key = _normalize_identity(self.key)
        normalized_aliases: set[str] = set()
        for alias in self.aliases:
            if not alias or alias != alias.strip():
                raise ValueError(
                    "override aliases must contain non-empty trimmed strings"
                )
            normalized = _normalize_identity(alias)
            if normalized == normalized_key:
                raise ValueError("override alias duplicates the override key")
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


@dataclass(frozen=True, slots=True)
class OverrideCatalog:
    schema_version: int
    shows: tuple[ShowOverride, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SUPPORTED_OVERRIDE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported override schema_version: "
                f"{self.schema_version}; expected {SUPPORTED_OVERRIDE_SCHEMA_VERSION}"
            )

        identities: dict[str, str] = {}
        provider_ids: dict[int, str] = {}
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

            if show.tvmaze_id is not None:
                owner = provider_ids.get(show.tvmaze_id)
                if owner is not None and owner != show.key:
                    raise ValueError(
                        "override tvmaze_id is assigned to multiple entries: "
                        f"{show.tvmaze_id}"
                    )
                provider_ids[show.tvmaze_id] = show.key

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
                    "title_preference": show.title_preference.value,
                    "tvmaze_id": show.tvmaze_id,
                    "year": show.year,
                }
            )

        payload = {
            "schema_version": self.schema_version,
            "shows": canonical_shows,
        }
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


def _parse_override(raw: dict[str, Any]) -> ShowOverride:
    allowed = {
        "key",
        "tvmaze_id",
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

    tvmaze_id = raw.get("tvmaze_id")
    year = raw.get("year")
    preferred_title = raw.get("preferred_title")
    if tvmaze_id is not None and not _is_plain_int(tvmaze_id):
        raise ValueError("override tvmaze_id must be an integer")
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
        tvmaze_id=tvmaze_id,
        aliases=tuple(aliases),
        year=year,
        numbering_mode=numbering_mode,
        title_preference=title_preference,
        preferred_title=preferred_title,
    )


def load_overrides(path: Path | None = None) -> OverrideCatalog:
    payload = path.read_bytes() if path is not None else _read_default_overrides()
    try:
        raw = tomllib.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("override file must be valid UTF-8") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid override TOML: {exc}") from exc

    allowed_top_level = {"schema_version", "shows"}
    unknown_top_level = set(raw) - allowed_top_level
    if unknown_top_level:
        raise ValueError(
            f"unknown top-level override fields: {sorted(unknown_top_level)}"
        )

    schema_version = raw.get("schema_version")
    shows = raw.get("shows", [])
    if not _is_plain_int(schema_version):
        raise ValueError("override schema_version must be an integer")
    if not isinstance(shows, list) or not all(
        isinstance(show, dict) for show in shows
    ):
        raise ValueError("override shows must be an array of tables")

    return OverrideCatalog(
        schema_version=schema_version,
        shows=tuple(_parse_override(show) for show in shows),
    )
