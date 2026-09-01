from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, cast

from .models import ProviderIdentity
from .tvmaze_cache import TVMAZE_PROVIDER, JsonGetter, TvmazeCatalogCache


@dataclass(frozen=True, slots=True)
class ProviderShow:
    identity: ProviderIdentity
    title: str
    year: int | None

    def __post_init__(self) -> None:
        title = self.title.strip()
        if not title:
            raise ValueError("provider show title cannot be empty")
        if self.year is not None and self.year < 1800:
            raise ValueError("provider show year is outside the supported range")
        object.__setattr__(self, "title", title)


@dataclass(frozen=True, slots=True, init=False)
class ProviderEpisode:
    identity: ProviderIdentity
    season: int
    number: int | None
    title: str
    airdate: str | None
    episode_type: str | None

    def __init__(
        self,
        identity: ProviderIdentity | None = None,
        season: int = 0,
        number: int | None = None,
        title: str = "",
        airdate: str | None = None,
        episode_type: str | None = None,
        *,
        tvmaze_episode_id: int | None = None,
    ) -> None:
        if identity is None:
            if tvmaze_episode_id is None:
                raise ValueError("provider episode identity is required")
            identity = ProviderIdentity.tvmaze(tvmaze_episode_id)
        elif tvmaze_episode_id is not None:
            legacy_identity = ProviderIdentity.tvmaze(tvmaze_episode_id)
            if identity != legacy_identity:
                raise ValueError("conflicting provider episode identities")
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "season", season)
        object.__setattr__(self, "number", number)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "airdate", airdate)
        object.__setattr__(self, "episode_type", episode_type)
        self.__post_init__()

    def __post_init__(self) -> None:
        title = self.title.strip()
        if self.season < 0:
            raise ValueError("provider episode season cannot be negative")
        if self.number is not None and self.number < 0:
            raise ValueError("provider episode number cannot be negative")
        if not title:
            raise ValueError("provider episode title cannot be empty")
        object.__setattr__(self, "title", title)
        if self.airdate is not None:
            try:
                normalized = date.fromisoformat(self.airdate).isoformat()
            except ValueError as exc:
                raise ValueError(
                    "provider episode airdate must use YYYY-MM-DD"
                ) from exc
            if normalized != self.airdate:
                raise ValueError("provider episode airdate must be canonical")
        if self.episode_type is not None:
            episode_type = self.episode_type.strip().casefold()
            if not episode_type:
                raise ValueError("provider episode type cannot be empty")
            object.__setattr__(self, "episode_type", episode_type)

    @property
    def provider(self) -> str:
        return self.identity.provider

    @property
    def provider_id(self) -> str:
        return self.identity.value

    @property
    def tvmaze_episode_id(self) -> int:
        return self.identity.require_positive_int(TVMAZE_PROVIDER)


@dataclass(frozen=True, slots=True)
class ProviderSearchSnapshot:
    provider: str
    request_key: str
    cache_snapshot_id: str
    shows: tuple[ProviderShow, ...]
    unresolved_reason: str | None = None
    retrieved_at: str | None = None

    def __post_init__(self) -> None:
        provider = ProviderIdentity.normalize_provider(self.provider)
        if provider != self.provider:
            raise ValueError("provider snapshot name must already be normalized")
        if not self.request_key or not self.cache_snapshot_id:
            raise ValueError("provider search snapshot identity cannot be empty")
        if any(show.identity.provider != provider for show in self.shows):
            raise ValueError("provider search snapshot contains a foreign identity")
        if self.unresolved_reason is not None and self.shows:
            raise ValueError("unresolved provider search snapshots cannot carry shows")

    @property
    def resolved(self) -> bool:
        return self.unresolved_reason is None

    @property
    def snapshot_identity(self) -> str:
        return f"{self.provider}:{self.request_key}:{self.cache_snapshot_id}"


@dataclass(frozen=True, slots=True)
class ProviderEpisodeCatalog:
    provider: str
    request_key: str
    cache_snapshot_id: str
    show_identity: ProviderIdentity
    episodes: tuple[ProviderEpisode, ...]
    errors: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    unresolved_reason: str | None = None
    retrieved_at: str | None = None

    def __post_init__(self) -> None:
        provider = ProviderIdentity.normalize_provider(self.provider)
        if provider != self.provider:
            raise ValueError("provider catalog name must already be normalized")
        if not self.request_key or not self.cache_snapshot_id:
            raise ValueError("provider catalog snapshot identity cannot be empty")
        if self.show_identity.provider != provider:
            raise ValueError("provider catalog show identity is foreign")
        if any(episode.identity.provider != provider for episode in self.episodes):
            raise ValueError("provider catalog contains a foreign episode identity")
        if self.unresolved_reason is not None and (
            self.episodes or self.errors or self.diagnostics
        ):
            raise ValueError(
                "unresolved provider catalogs cannot carry normalized data"
            )

    @property
    def resolved(self) -> bool:
        return self.unresolved_reason is None

    @property
    def snapshot_identity(self) -> str:
        return f"{self.provider}:{self.request_key}:{self.cache_snapshot_id}"


class MetadataProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    def search_shows(self, title: str) -> ProviderSearchSnapshot: ...

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog: ...


def _tvmaze_show_candidates(response: object) -> tuple[ProviderShow, ...]:
    if not isinstance(response, list):
        return ()

    candidates: dict[tuple[str, str, int | None], ProviderShow] = {}
    for item in response:
        if not isinstance(item, dict):
            continue
        raw_item = cast(dict[str, Any], item)
        show = raw_item.get("show")
        if not isinstance(show, dict):
            continue
        raw_show = cast(dict[str, Any], show)
        provider_id = raw_show.get("id")
        title = raw_show.get("name")
        premiered = raw_show.get("premiered")
        if not isinstance(provider_id, int) or provider_id <= 0:
            continue
        if not isinstance(title, str) or not title.strip():
            continue

        year = None
        if isinstance(premiered, str):
            match = re.match(r"^(\d{4})-", premiered)
            if match is not None:
                year = int(match.group(1))
        candidate = ProviderShow(
            identity=ProviderIdentity.tvmaze(provider_id),
            title=title,
            year=year,
        )
        candidates[(candidate.identity.value, candidate.title, candidate.year)] = (
            candidate
        )

    return tuple(
        sorted(
            candidates.values(),
            key=lambda value: (
                value.title.casefold(),
                value.title,
                value.year if value.year is not None else -1,
                value.identity.value,
            ),
        )
    )


def _normalize_optional_airdate(
    value: object,
    index: int,
    diagnostics: list[str],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        diagnostics.append(f"invalid-catalog-airdate:{index}")
        return None
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        diagnostics.append(f"invalid-catalog-airdate:{index}")
        return None
    return value


def _normalize_optional_episode_type(
    value: object,
    index: int,
    diagnostics: list[str],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        diagnostics.append(f"invalid-catalog-type:{index}")
        return None
    return value


def _tvmaze_episode_catalog(
    response: object,
) -> tuple[
    tuple[ProviderEpisode, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if not isinstance(response, list):
        return (), ("episode-catalog-is-not-a-list",), ()

    episodes: list[ProviderEpisode] = []
    errors: list[str] = []
    diagnostics: list[str] = []
    for index, item in enumerate(response):
        if not isinstance(item, dict):
            errors.append(f"invalid-catalog-entry:{index}")
            continue
        raw = cast(dict[str, Any], item)
        episode_id = raw.get("id")
        season = raw.get("season")
        number = raw.get("number")
        title = raw.get("name")
        if (
            not isinstance(episode_id, int)
            or isinstance(episode_id, bool)
            or episode_id <= 0
        ):
            errors.append(f"invalid-catalog-episode-id:{index}")
            continue
        if not isinstance(season, int) or isinstance(season, bool) or season < 0:
            errors.append(f"invalid-catalog-season:{index}")
            continue
        if number is not None and (
            not isinstance(number, int) or isinstance(number, bool) or number < 0
        ):
            errors.append(f"invalid-catalog-number:{index}")
            continue
        if not isinstance(title, str) or not title.strip():
            errors.append(f"invalid-catalog-title:{index}")
            continue

        airdate = _normalize_optional_airdate(raw.get("airdate"), index, diagnostics)
        episode_type = _normalize_optional_episode_type(
            raw.get("type"), index, diagnostics
        )
        episodes.append(
            ProviderEpisode(
                identity=ProviderIdentity.tvmaze(episode_id),
                season=season,
                number=number,
                title=title,
                airdate=airdate,
                episode_type=episode_type,
            )
        )

    by_id: dict[ProviderIdentity, list[ProviderEpisode]] = defaultdict(list)
    by_coordinate: dict[tuple[int, int], list[ProviderEpisode]] = defaultdict(list)
    for episode in episodes:
        by_id[episode.identity].append(episode)
        if episode.number is not None:
            by_coordinate[(episode.season, episode.number)].append(episode)

    for identity, matches in sorted(by_id.items(), key=lambda item: item[0].key):
        if len(matches) > 1:
            errors.append(f"duplicate-provider-episode-id:{identity.value}")
    for (season, number), matches in sorted(by_coordinate.items()):
        if len(matches) > 1:
            errors.append(f"duplicate-aired-coordinate:S{season:02d}E{number:02d}")

    return (
        tuple(
            sorted(
                episodes,
                key=lambda episode: (
                    episode.season,
                    episode.number if episode.number is not None else 10**9,
                    episode.identity.key,
                ),
            )
        ),
        tuple(errors),
        tuple(diagnostics),
    )


class TvmazeProviderAdapter:
    """Normalize TVMaze cache records behind the provider-neutral boundary."""

    provider_name = TVMAZE_PROVIDER

    def __init__(self, cache: TvmazeCatalogCache, getter: JsonGetter) -> None:
        self._cache = cache
        self._getter = getter

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        record = self._cache.search_show(title, self._getter)
        if not record.resolved:
            return ProviderSearchSnapshot(
                provider=self.provider_name,
                request_key=record.request_key,
                cache_snapshot_id=record.snapshot_id,
                shows=(),
                unresolved_reason=record.unresolved_reason
                or "provider-search-unresolved",
                retrieved_at=record.retrieved_at,
            )
        return ProviderSearchSnapshot(
            provider=self.provider_name,
            request_key=record.request_key,
            cache_snapshot_id=record.snapshot_id,
            shows=_tvmaze_show_candidates(record.response),
            retrieved_at=record.retrieved_at,
        )

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        if show_identity.provider != self.provider_name:
            raise ValueError(
                f"provider identity is {show_identity.provider!r}, "
                f"expected {self.provider_name!r}"
            )
        tvmaze_id = show_identity.require_positive_int(self.provider_name)
        record = self._cache.episode_catalog(tvmaze_id, self._getter)
        if not record.resolved:
            return ProviderEpisodeCatalog(
                provider=self.provider_name,
                request_key=record.request_key,
                cache_snapshot_id=record.snapshot_id,
                show_identity=show_identity,
                episodes=(),
                unresolved_reason=record.unresolved_reason
                or "provider-catalog-unresolved",
                retrieved_at=record.retrieved_at,
            )
        episodes, errors, diagnostics = _tvmaze_episode_catalog(record.response)
        return ProviderEpisodeCatalog(
            provider=self.provider_name,
            request_key=record.request_key,
            cache_snapshot_id=record.snapshot_id,
            show_identity=show_identity,
            episodes=episodes,
            errors=errors,
            diagnostics=diagnostics,
            retrieved_at=record.retrieved_at,
        )
