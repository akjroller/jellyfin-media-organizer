from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .episode_assignment import (
    AssignmentStatus,
    ProviderEpisode,
    SourceEpisodeAssignment,
)
from .models import CanonicalShow, ExtraDecision


class DestinationStatus(StrEnum):
    READY = "ready"
    UNRESOLVED = "unresolved"


class JellyfinProvider(StrEnum):
    TMDB = "tmdb"
    TVDB = "tvdb"
    IMDB = "imdb"


@dataclass(frozen=True, slots=True)
class JellyfinProviderIdentifier:
    provider: JellyfinProvider
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if not value:
            raise ValueError("provider identifier value cannot be empty")
        if self.provider in {JellyfinProvider.TMDB, JellyfinProvider.TVDB}:
            if not value.isascii() or not value.isdigit() or int(value) <= 0:
                raise ValueError("TMDb/TVDb identifiers must be positive integers")
        elif not re.fullmatch(r"tt\d+", value, flags=re.IGNORECASE):
            raise ValueError("IMDb identifiers must use the tt123456 form")
        object.__setattr__(self, "value", value.casefold())

    @property
    def jellyfin_tag(self) -> str:
        return f"[{self.provider.value}id-{self.value}]"


@dataclass(frozen=True, slots=True)
class DestinationPolicy:
    include_year: bool = True
    max_path_length: int = 240
    max_component_length: int = 180

    def __post_init__(self) -> None:
        if self.max_path_length < 80:
            raise ValueError("max_path_length must be at least 80 characters")
        if not 32 <= self.max_component_length <= 255:
            raise ValueError("max_component_length must be between 32 and 255")


@dataclass(frozen=True, slots=True)
class DestinationDecision:
    source_key: str
    status: DestinationStatus
    relative_path: str | None
    collision_key: str | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.source_key:
            raise ValueError("destination decision requires a source_key")
        if not self.reasons:
            raise ValueError("destination decision requires at least one reason")
        if self.status is DestinationStatus.READY:
            if self.relative_path is None or self.collision_key is None:
                raise ValueError("ready destinations require a path and collision key")
        elif self.relative_path is not None or self.collision_key is not None:
            raise ValueError("unresolved destinations cannot carry a final path")


@dataclass(frozen=True, slots=True)
class DestinationCollision:
    collision_key: str
    source_keys: tuple[str, ...]
    relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.source_keys) < 2:
            raise ValueError("destination collisions require at least two sources")
        if len(self.source_keys) != len(self.relative_paths):
            raise ValueError("collision sources and paths must have equal length")


_FORBIDDEN = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_EXTRA_FOLDERS: dict[str, str] = {
    "trailer": "trailers",
    "featurette": "featurettes",
    "interview": "interviews",
    "behind-the-scenes": "behind the scenes",
    "deleted-scene": "deleted scenes",
    "clip": "clips",
    "extra": "extras",
    "creditless-opening": "extras",
    "creditless-ending": "extras",
}
_EXTRA_DEFAULT_TITLES: dict[str, str] = {
    "trailer": "Trailer",
    "featurette": "Featurette",
    "interview": "Interview",
    "behind-the-scenes": "Behind the Scenes",
    "deleted-scene": "Deleted Scene",
    "clip": "Clip",
    "extra": "Extra",
    "creditless-opening": "Creditless Opening",
    "creditless-ending": "Creditless Ending",
}
_HASH_SUFFIX_LENGTH = 14


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def _reserved_basename(value: str) -> str:
    return value.split(".", 1)[0].rstrip(" .").casefold()


def _escape_component(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    trailing_start = len(normalized.rstrip(" ."))
    escaped: list[str] = []
    for index, character in enumerate(normalized):
        codepoint = ord(character)
        if character == "~":
            escaped.append("~~")
        elif (
            character in _FORBIDDEN
            or codepoint < 32
            or (index >= trailing_start and character in " .")
        ):
            escaped.append(f"~{codepoint:04X}")
        else:
            escaped.append(character)

    result = "".join(escaped)
    if not result:
        result = "~EMPTY~"
    if _reserved_basename(normalized) in _WINDOWS_RESERVED:
        result = f"~R~{result}"
    return result


def _truncate_component(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    suffix = f"~h{_digest(value)}"
    if limit <= len(suffix):
        raise ValueError("component limit is too small for deterministic truncation")
    return f"{value[: limit - len(suffix)]}{suffix}"


def sanitize_component(value: str, *, max_length: int = 180) -> str:
    """Return a deterministic Windows/POSIX-safe path component.

    Unsafe characters are encoded instead of dropped, preventing two distinct raw
    values such as ``A:B`` and ``A?B`` from silently becoming the same component.
    Literal ``~`` is escaped so the encoding namespace remains unambiguous.
    """

    if not 32 <= max_length <= 255:
        raise ValueError("max_length must be between 32 and 255")
    return _truncate_component(_escape_component(value), max_length)


def _normalized_extension(extension: str) -> str:
    normalized = unicodedata.normalize("NFC", extension).casefold()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", normalized):
        raise ValueError("source extension must be a simple .extension value")
    return normalized


def _provider_tags(
    provider_ids: Iterable[JellyfinProviderIdentifier],
) -> tuple[str, ...]:
    identifiers = tuple(provider_ids)
    keyed: dict[JellyfinProvider, JellyfinProviderIdentifier] = {}
    for identifier in identifiers:
        existing = keyed.get(identifier.provider)
        if existing is not None and existing != identifier:
            raise ValueError(
                f"conflicting {identifier.provider.value} provider identifiers"
            )
        keyed[identifier.provider] = identifier
    return tuple(
        keyed[provider].jellyfin_tag
        for provider in sorted(keyed, key=lambda item: item.value)
    )


def _series_label(
    show: CanonicalShow,
    policy: DestinationPolicy,
    provider_ids: Iterable[JellyfinProviderIdentifier],
    *,
    include_provider_tags: bool,
) -> str:
    parts = [show.title]
    if policy.include_year and show.year is not None:
        parts.append(f"({show.year})")
    if include_provider_tags:
        parts.extend(_provider_tags(provider_ids))
    return " ".join(parts)


def _collision_key(relative_path: str) -> str:
    return unicodedata.normalize("NFC", relative_path).casefold()


def _fit_relative_path(
    *,
    series_folder_raw: str,
    middle_folder_raw: str,
    filename_stem_raw: str,
    extension: str,
    policy: DestinationPolicy,
) -> tuple[str | None, tuple[str, ...]]:
    series_folder = sanitize_component(
        series_folder_raw, max_length=policy.max_component_length
    )
    middle_folder = sanitize_component(
        middle_folder_raw, max_length=policy.max_component_length
    )
    filename_stem = sanitize_component(
        filename_stem_raw, max_length=policy.max_component_length
    )

    reasons: list[str] = []
    if series_folder != unicodedata.normalize("NFC", series_folder_raw):
        reasons.append("series-component-sanitized")
    if middle_folder != unicodedata.normalize("NFC", middle_folder_raw):
        reasons.append("middle-component-sanitized")
    if filename_stem != unicodedata.normalize("NFC", filename_stem_raw):
        reasons.append("filename-component-sanitized")

    def render() -> str:
        return f"{series_folder}/{middle_folder}/{filename_stem}{extension}"

    relative_path = render()
    if len(relative_path) <= policy.max_path_length:
        return relative_path, tuple(reasons)

    excess = len(relative_path) - policy.max_path_length
    target_filename = max(32, len(filename_stem) - excess)
    shortened_filename = _truncate_component(filename_stem, target_filename)
    if shortened_filename != filename_stem:
        filename_stem = shortened_filename
        reasons.append("filename-shortened-for-path-limit")
    relative_path = render()
    if len(relative_path) <= policy.max_path_length:
        return relative_path, tuple(reasons)

    excess = len(relative_path) - policy.max_path_length
    target_series = max(32, len(series_folder) - excess)
    shortened_series = _truncate_component(series_folder, target_series)
    if shortened_series != series_folder:
        series_folder = shortened_series
        reasons.append("series-shortened-for-path-limit")
    relative_path = render()
    if len(relative_path) <= policy.max_path_length:
        return relative_path, tuple(reasons)

    return None, (*reasons, "path-length-limit-cannot-be-satisfied")


def _unresolved(source_key: str, *reasons: str) -> DestinationDecision:
    return DestinationDecision(
        source_key=source_key,
        status=DestinationStatus.UNRESOLVED,
        relative_path=None,
        collision_key=None,
        reasons=tuple(reasons),
    )


def _ready(
    source_key: str,
    relative_path: str,
    *reasons: str,
) -> DestinationDecision:
    return DestinationDecision(
        source_key=source_key,
        status=DestinationStatus.READY,
        relative_path=relative_path,
        collision_key=_collision_key(relative_path),
        reasons=tuple(reasons),
    )


def _episode_token(
    episodes: tuple[ProviderEpisode, ...],
) -> tuple[str | None, str | None]:
    if not episodes:
        return None, "episode-assignment-has-no-provider-episodes"
    if any(episode.number is None for episode in episodes):
        return None, "provider-episode-number-is-missing"

    seasons = {episode.season for episode in episodes}
    if len(seasons) != 1:
        return None, "multi-episode-source-spans-seasons"

    season = episodes[0].season
    numbers = tuple(
        int(episode.number) for episode in episodes if episode.number is not None
    )
    if len(set(numbers)) != len(numbers):
        return None, "duplicate-provider-episode-number-in-source"
    if len(numbers) == 1:
        return f"S{season:02d}E{numbers[0]:02d}", None

    expected = tuple(range(numbers[0], numbers[-1] + 1))
    if numbers != expected:
        return None, "multi-episode-source-is-not-one-contiguous-ascending-range"
    return f"S{season:02d}E{numbers[0]:02d}-E{numbers[-1]:02d}", None


def _episode_title(episodes: tuple[ProviderEpisode, ...]) -> str:
    titles = tuple(dict.fromkeys(episode.title.strip() for episode in episodes))
    return " + ".join(title for title in titles if title)


def build_episode_destination(
    show: CanonicalShow,
    assignment: SourceEpisodeAssignment,
    source_extension: str,
    *,
    provider_ids: Iterable[JellyfinProviderIdentifier] = (),
    policy: DestinationPolicy | None = None,
) -> DestinationDecision:
    """Build one Jellyfin-relative destination without reparsing the source name."""

    active_policy = policy or DestinationPolicy()
    if assignment.status is not AssignmentStatus.MATCHED:
        return _unresolved(
            assignment.source_key,
            f"episode-assignment-not-matched:{assignment.status.value}",
        )

    token, token_error = _episode_token(assignment.episodes)
    if token_error is not None or token is None:
        return _unresolved(
            assignment.source_key, token_error or "invalid-episode-token"
        )

    try:
        extension = _normalized_extension(source_extension)
        provider_tags = _provider_tags(provider_ids)
    except ValueError as exc:
        return _unresolved(assignment.source_key, f"invalid-destination-input:{exc}")

    series_folder_raw = _series_label(
        show,
        active_policy,
        provider_ids,
        include_provider_tags=True,
    )
    series_filename_raw = _series_label(
        show,
        active_policy,
        (),
        include_provider_tags=False,
    )
    season_folder_raw = f"Season {assignment.episodes[0].season:02d}"
    episode_title = _episode_title(assignment.episodes)
    filename_stem_raw = f"{series_filename_raw} {token}"
    if episode_title:
        filename_stem_raw = f"{filename_stem_raw} - {episode_title}"

    relative_path, path_reasons = _fit_relative_path(
        series_folder_raw=series_folder_raw,
        middle_folder_raw=season_folder_raw,
        filename_stem_raw=filename_stem_raw,
        extension=extension,
        policy=active_policy,
    )
    if relative_path is None:
        return _unresolved(
            assignment.source_key,
            *path_reasons,
            f"configured-max-path-length:{active_policy.max_path_length}",
        )

    reasons = [
        "jellyfin-series-season-episode-layout",
        f"numbering-mode:{show.numbering_mode.value}",
        f"canonical-tvmaze-id:{show.tvmaze_id}",
        f"episode-token:{token}",
    ]
    reasons.extend(f"jellyfin-provider-tag:{tag}" for tag in provider_tags)
    if show.tvmaze_id and not provider_tags:
        reasons.append("tvmaze-id-retained-as-audit-only-identity")
    reasons.extend(path_reasons)
    return _ready(assignment.source_key, relative_path, *reasons)


def build_extra_destination(
    show: CanonicalShow,
    *,
    source_key: str,
    extra: ExtraDecision,
    source_extension: str,
    display_title: str | None = None,
    provider_ids: Iterable[JellyfinProviderIdentifier] = (),
    policy: DestinationPolicy | None = None,
) -> DestinationDecision:
    """Build one Jellyfin-compatible series-level extra destination."""

    active_policy = policy or DestinationPolicy()
    try:
        extension = _normalized_extension(source_extension)
        provider_tags = _provider_tags(provider_ids)
    except ValueError as exc:
        return _unresolved(source_key, f"invalid-destination-input:{exc}")

    extra_kind = extra.kind.casefold()
    folder = _EXTRA_FOLDERS.get(extra_kind, "extras")
    title = (display_title or _EXTRA_DEFAULT_TITLES.get(extra_kind, "Extra")).strip()
    if not title:
        return _unresolved(source_key, "extra-display-title-is-empty")

    series_folder_raw = _series_label(
        show,
        active_policy,
        provider_ids,
        include_provider_tags=True,
    )
    relative_path, path_reasons = _fit_relative_path(
        series_folder_raw=series_folder_raw,
        middle_folder_raw=folder,
        filename_stem_raw=title,
        extension=extension,
        policy=active_policy,
    )
    if relative_path is None:
        return _unresolved(
            source_key,
            *path_reasons,
            f"configured-max-path-length:{active_policy.max_path_length}",
        )

    reasons = [
        "jellyfin-series-extra-folder-layout",
        f"extra-kind:{extra_kind}",
        f"canonical-tvmaze-id:{show.tvmaze_id}",
    ]
    reasons.extend(f"jellyfin-provider-tag:{tag}" for tag in provider_tags)
    if extra_kind not in _EXTRA_FOLDERS:
        reasons.append("unknown-extra-kind-mapped-to-generic-extras-folder")
    reasons.extend(path_reasons)
    return _ready(source_key, relative_path, *reasons)


def find_destination_collisions(
    decisions: Iterable[DestinationDecision],
) -> tuple[DestinationCollision, ...]:
    """Report case-insensitive destination convergence without choosing a winner."""

    grouped: dict[str, list[DestinationDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.status is DestinationStatus.READY:
            assert decision.collision_key is not None
            grouped[decision.collision_key].append(decision)

    collisions: list[DestinationCollision] = []
    for collision_key, matches in sorted(grouped.items()):
        if len(matches) < 2:
            continue
        ordered = sorted(
            matches,
            key=lambda decision: (decision.source_key.casefold(), decision.source_key),
        )
        collisions.append(
            DestinationCollision(
                collision_key=collision_key,
                source_keys=tuple(decision.source_key for decision in ordered),
                relative_paths=tuple(
                    decision.relative_path or "" for decision in ordered
                ),
            )
        )
    return tuple(collisions)
