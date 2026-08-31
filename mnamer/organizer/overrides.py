"""Versioned, data-driven show aliases and numbering policies."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import cast

from mnamer.organizer.models import NumberingMode, TitlePreference

OVERRIDE_SCHEMA_VERSION = 1
DEFAULT_OVERRIDE_RESOURCE = "show-overrides.v1.json"


def normalize_show_alias(value: str) -> str:
    """Return a conservative key for alias lookup."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("&", " and ")
    return re.sub(r"[\W_]+", " ", normalized).strip()


@dataclass(frozen=True, slots=True)
class ShowOverride:
    """One visible show-specific resolution and numbering policy."""

    canonical_title: str
    aliases: tuple[str, ...]
    numbering_mode: NumberingMode
    title_preference: TitlePreference
    tvmaze_id: int | None = None
    year: int | None = None

    def __post_init__(self) -> None:
        if not self.canonical_title.strip():
            raise ValueError("canonical_title must not be blank")
        object.__setattr__(self, "numbering_mode", NumberingMode(self.numbering_mode))
        object.__setattr__(
            self, "title_preference", TitlePreference(self.title_preference)
        )

        aliases = tuple(alias.strip() for alias in self.aliases)
        if any(not alias for alias in aliases):
            raise ValueError("aliases must not contain blank values")
        alias_keys = [normalize_show_alias(alias) for alias in aliases]
        if len(alias_keys) != len(set(alias_keys)):
            raise ValueError(f"duplicate aliases for {self.canonical_title}")
        object.__setattr__(self, "aliases", aliases)

        if self.tvmaze_id is not None and self.tvmaze_id <= 0:
            raise ValueError("tvmaze_id must be positive")
        if self.year is not None and not 1800 <= self.year <= 3000:
            raise ValueError("year must be between 1800 and 3000")

    @property
    def lookup_names(self) -> tuple[str, ...]:
        """Return canonical title plus aliases without normalized duplicates."""
        names = (self.canonical_title, *self.aliases)
        unique: dict[str, str] = {}
        for name in names:
            unique.setdefault(normalize_show_alias(name), name)
        return tuple(unique.values())


@dataclass(frozen=True, slots=True)
class OverrideCatalog:
    """Validated override configuration with deterministic alias lookup."""

    schema_version: int
    shows: tuple[ShowOverride, ...]

    def __post_init__(self) -> None:
        if self.schema_version != OVERRIDE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported override schema version: {self.schema_version}"
            )
        object.__setattr__(self, "shows", tuple(self.shows))

        owner_by_alias: dict[str, str] = {}
        for show in self.shows:
            for name in show.lookup_names:
                key = normalize_show_alias(name)
                owner = owner_by_alias.get(key)
                if owner is not None and owner != show.canonical_title:
                    raise ValueError(
                        f"alias {name!r} belongs to both {owner!r} and "
                        f"{show.canonical_title!r}"
                    )
                owner_by_alias[key] = show.canonical_title

    def find(self, source_title: str) -> ShowOverride | None:
        """Return the exact normalized alias override, if configured."""
        lookup_key = normalize_show_alias(source_title)
        for show in self.shows:
            if any(
                normalize_show_alias(name) == lookup_key for name in show.lookup_names
            ):
                return show
        return None


def load_show_overrides(path: Path | None = None) -> OverrideCatalog:
    """Load the packaged catalog or a caller-supplied JSON override file."""
    if path is None:
        resource = files("mnamer.organizer").joinpath(DEFAULT_OVERRIDE_RESOURCE)
        raw_text = resource.read_text(encoding="utf-8")
    else:
        raw_text = path.read_text(encoding="utf-8")

    try:
        raw_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid override JSON: {exc.msg}") from exc

    data = _mapping(raw_data, "override catalog")
    raw_shows = _sequence(data.get("shows"), "shows")
    return OverrideCatalog(
        schema_version=_integer(data.get("schema_version"), "schema_version"),
        shows=tuple(_decode_override(value) for value in raw_shows),
    )


def _decode_override(value: object) -> ShowOverride:
    data = _mapping(value, "show override")
    raw_aliases = _sequence(data.get("aliases"), "aliases")
    return ShowOverride(
        canonical_title=_text(data.get("canonical_title"), "canonical_title"),
        aliases=tuple(_text(alias, "aliases[]") for alias in raw_aliases),
        tvmaze_id=_optional_integer(data.get("tvmaze_id"), "tvmaze_id"),
        year=_optional_integer(data.get("year"), "year"),
        numbering_mode=NumberingMode(
            _text(data.get("numbering_mode"), "numbering_mode")
        ),
        title_preference=TitlePreference(
            _text(data.get("title_preference"), "title_preference")
        ),
    )


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


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _optional_integer(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field_name)
