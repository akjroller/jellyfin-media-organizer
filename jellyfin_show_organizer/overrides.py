from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .models import NumberingMode, ProviderIdentity, TitlePreference

OVERRIDES_RESOURCE = "data/overrides-v1.toml"


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
        if not self.key:
            raise ValueError("override key cannot be empty")
        if self.year is not None and self.year < 1800:
            raise ValueError("override year is outside the supported range")
        if (
            self.title_preference is TitlePreference.OVERRIDE
            and not self.preferred_title
        ):
            raise ValueError("title_preference='override' requires preferred_title")

    @property
    def provider(self) -> str | None:
        return (
            self.provider_identity.provider
            if self.provider_identity is not None
            else None
        )

    @property
    def provider_id(self) -> str | None:
        return (
            self.provider_identity.value
            if self.provider_identity is not None
            else None
        )

    @property
    def tvmaze_id(self) -> int | None:
        """Compatibility alias for existing TVMaze override files/callers."""

        if self.provider_identity is None:
            return None
        if self.provider_identity.provider != "tvmaze":
            return None
        return self.provider_identity.require_positive_int("tvmaze")


@dataclass(frozen=True, slots=True)
class OverrideCatalog:
    schema_version: int
    shows: tuple[ShowOverride, ...]

    def __post_init__(self) -> None:
        if self.schema_version <= 0:
            raise ValueError("override schema_version must be positive")
        keys = [show.key.casefold() for show in self.shows]
        if len(keys) != len(set(keys)):
            raise ValueError("override keys must be unique case-insensitively")

    def get(self, key: str) -> ShowOverride | None:
        normalized = key.casefold()
        return next(
            (show for show in self.shows if show.key.casefold() == normalized),
            None,
        )


def _read_default_overrides() -> bytes:
    resource = files("jellyfin_show_organizer").joinpath(OVERRIDES_RESOURCE)
    return resource.read_bytes()


def _provider_identity(raw: dict[str, Any]) -> ProviderIdentity | None:
    tvmaze_id = raw.get("tvmaze_id")
    provider = raw.get("provider")
    provider_id = raw.get("provider_id")

    if tvmaze_id is not None and not isinstance(tvmaze_id, int):
        raise ValueError("override tvmaze_id must be an integer")
    if provider is not None and not isinstance(provider, str):
        raise ValueError("override provider must be a string")
    if provider_id is not None and not isinstance(provider_id, str | int):
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
    if year is not None and not isinstance(year, int):
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


def load_overrides(path: Path | None = None) -> OverrideCatalog:
    payload = path.read_bytes() if path is not None else _read_default_overrides()
    raw = tomllib.loads(payload.decode("utf-8"))

    schema_version = raw.get("schema_version")
    shows = raw.get("shows", [])
    if not isinstance(schema_version, int):
        raise ValueError("override schema_version must be an integer")
    if not isinstance(shows, list) or not all(isinstance(show, dict) for show in shows):
        raise ValueError("override shows must be an array of tables")

    return OverrideCatalog(
        schema_version=schema_version,
        shows=tuple(_parse_override(show) for show in shows),
    )
