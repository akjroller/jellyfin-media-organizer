from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol, cast

from .models import ProviderIdentity
from .providers import MetadataProvider, TvmazeProviderAdapter
from .tvmaze_alias_cache import TvmazeAliasCache
from .tvmaze_cache import JsonGetter, TvmazeCatalogCache


@dataclass(frozen=True, slots=True)
class ProviderAliasSnapshot:
    provider: str
    request_key: str
    cache_snapshot_id: str
    show_identity: ProviderIdentity
    aliases: tuple[str, ...]
    errors: tuple[str, ...] = ()
    unresolved_reason: str | None = None

    def __post_init__(self) -> None:
        provider = ProviderIdentity.normalize_provider(self.provider)
        if provider != self.provider:
            raise ValueError("provider alias snapshot name must already be normalized")
        if self.show_identity.provider != provider:
            raise ValueError("provider alias snapshot contains a foreign show identity")
        if not self.request_key or not self.cache_snapshot_id:
            raise ValueError("provider alias snapshot identity cannot be empty")
        if self.unresolved_reason is not None and (self.aliases or self.errors):
            raise ValueError("unresolved provider alias snapshots cannot carry data")

    @property
    def resolved(self) -> bool:
        return self.unresolved_reason is None


class AliasMetadataProvider(MetadataProvider, Protocol):
    def show_aliases(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderAliasSnapshot: ...


def _tvmaze_aliases(response: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(response, list):
        return (), ("alias-response-is-not-a-list",)

    aliases: dict[str, str] = {}
    errors: list[str] = []
    for index, item in enumerate(response):
        if not isinstance(item, dict):
            errors.append(f"invalid-alias-entry:{index}")
            continue
        raw = cast(dict[str, Any], item)
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"invalid-alias-name:{index}")
            continue
        trimmed = unicodedata.normalize("NFKC", name).strip()
        key = trimmed.casefold()
        aliases.setdefault(key, trimmed)

    return (
        tuple(sorted(aliases.values(), key=lambda value: (value.casefold(), value))),
        tuple(errors),
    )


class TvmazeAliasProviderAdapter(TvmazeProviderAdapter):
    """TVMaze provider adapter with lazily fetched deterministic AKA metadata."""

    def __init__(self, cache: TvmazeCatalogCache, getter: JsonGetter) -> None:
        super().__init__(cache, getter)
        self._alias_cache = TvmazeAliasCache(cache)
        self._alias_getter = getter

    def show_aliases(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderAliasSnapshot:
        if show_identity.provider != self.provider_name:
            raise ValueError(
                f"provider identity is {show_identity.provider!r}, "
                f"expected {self.provider_name!r}"
            )
        tvmaze_id = show_identity.require_positive_int(self.provider_name)
        record = self._alias_cache.show_aliases(tvmaze_id, self._alias_getter)
        if not record.resolved:
            return ProviderAliasSnapshot(
                provider=self.provider_name,
                request_key=record.request_key,
                cache_snapshot_id=record.snapshot_id,
                show_identity=show_identity,
                aliases=(),
                unresolved_reason=record.unresolved_reason
                or "provider-aliases-unresolved",
            )
        aliases, errors = _tvmaze_aliases(record.response)
        return ProviderAliasSnapshot(
            provider=self.provider_name,
            request_key=record.request_key,
            cache_snapshot_id=record.snapshot_id,
            show_identity=show_identity,
            aliases=aliases,
            errors=errors,
        )
