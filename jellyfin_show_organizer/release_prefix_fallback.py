from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from . import _show_resolver_core as _core
from .models import ParseResult, ProviderIdentity
from .overrides import OverrideCatalog
from .providers import (
    MetadataProvider,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)

_RELEASE_PREFIX = re.compile(
    r"^(?P<prefix>[A-Z0-9][A-Z0-9._]{1,15})-(?P<title>[^-].+)$"
)
_LOWER_RELEASE_PREFIX = re.compile(
    r"^(?P<prefix>[a-z][a-z0-9._]{4,14})-(?P<title>[^-].+)$"
)


def release_prefix_title(value: str) -> tuple[str, str] | None:
    """Return one conservative release-token/title split or ``None``."""

    match = _RELEASE_PREFIX.fullmatch(value.strip())
    lower_prefix = False
    if match is None:
        match = _LOWER_RELEASE_PREFIX.fullmatch(value.strip())
        lower_prefix = match is not None
    if match is None:
        return None

    prefix = match.group("prefix")
    title = match.group("title").strip(" ._-")
    if not title or not any(character.isalpha() for character in prefix):
        return None
    if (
        _RELEASE_PREFIX.fullmatch(title) is not None
        or _LOWER_RELEASE_PREFIX.fullmatch(title) is not None
    ):
        return None

    title_words = re.findall(r"[^\W\d_]+", title, flags=re.UNICODE)
    minimum_words = 4 if lower_prefix else 2
    if len(title_words) < minimum_words:
        return None
    if _core.normalize_show_identity(title) == _core.normalize_show_identity(value):
        return None
    return prefix, title


class _CombinedSearchProvider:
    """Replay one deterministic candidate union for original and stripped titles."""

    def __init__(
        self,
        provider: MetadataProvider,
        titles: tuple[str, ...],
        snapshot: ProviderSearchSnapshot,
    ) -> None:
        self._provider = provider
        self._titles = frozenset(titles)
        self._snapshot = snapshot

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        if title in self._titles:
            return self._snapshot
        return self._provider.search_shows(title)

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        return self._provider.episode_catalog(show_identity)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


def _annotate(
    result: _core.ShowResolution,
    reasons: tuple[str, ...],
    *,
    force_unresolved: bool = False,
) -> _core.ShowResolution:
    return replace(
        result,
        status=(
            _core.ResolutionStatus.UNRESOLVED if force_unresolved else result.status
        ),
        show=None if force_unresolved else result.show,
        evidence=replace(
            result.evidence,
            method=f"{result.evidence.method}+release-prefix-fallback",
            confidence=(0.0 if force_unresolved else result.evidence.confidence),
            reasons=(*result.evidence.reasons, *reasons),
        ),
    )


def resolve_release_prefix_fallback(
    source_key: str,
    parses: tuple[ParseResult, ...],
    overrides: OverrideCatalog,
    provider: MetadataProvider,
    result: _core.ShowResolution,
) -> tuple[_core.ShowResolution, MetadataProvider] | None:
    """Retry one release-prefixed show title and require catalog confirmation."""

    if (
        result.status is not _core.ResolutionStatus.UNRESOLVED
        or result.show is not None
    ):
        return None

    titles = _core._source_titles(parses)
    if _core._matching_overrides(source_key, titles, overrides):
        return None
    if _core._explicit_identities(parses, None):
        return None

    source_title = _core._representative_title(titles)
    if source_title is None:
        return None
    split = release_prefix_title(source_title)
    if split is None:
        return None
    prefix, stripped_title = split

    reasons: list[str] = [
        "release-prefix-fallback:attempted",
        f"release-prefix-fallback-prefix:{prefix.casefold()}",
        f"release-prefix-fallback-query:{_core.normalize_show_identity(stripped_title)}",
    ]

    original = provider.search_shows(source_title)
    reasons.append(f"release-prefix-original-request:{original.request_key}")
    if not original.resolved:
        return (
            _annotate(
                result,
                (
                    *reasons,
                    "release-prefix-fallback:original-search-indeterminate",
                    original.unresolved_reason or "provider-search-unresolved",
                ),
                force_unresolved=True,
            ),
            provider,
        )

    stripped = provider.search_shows(stripped_title)
    reasons.append(f"release-prefix-stripped-request:{stripped.request_key}")
    if not stripped.resolved:
        return (
            _annotate(
                result,
                (
                    *reasons,
                    "release-prefix-fallback:stripped-search-indeterminate",
                    stripped.unresolved_reason or "provider-search-unresolved",
                ),
                force_unresolved=True,
            ),
            provider,
        )

    candidates_by_identity: dict[ProviderIdentity, ProviderShow] = {}
    for snapshot in (original, stripped):
        for candidate in snapshot.shows:
            previous = candidates_by_identity.get(candidate.identity)
            if previous is not None and previous != candidate:
                return (
                    _annotate(
                        result,
                        (
                            *reasons,
                            "release-prefix-fallback:conflicting-candidate-metadata",
                            f"provider-identity:{candidate.identity.key}",
                        ),
                        force_unresolved=True,
                    ),
                    provider,
                )
            candidates_by_identity[candidate.identity] = candidate

    reasons.append("release-prefix-fallback:search-complete")
    if not stripped.shows:
        return _annotate(result, tuple(reasons)), provider

    combined = ProviderSearchSnapshot(
        provider=stripped.provider,
        request_key=f"{original.request_key}|{stripped.request_key}",
        cache_snapshot_id=f"{original.cache_snapshot_id}|{stripped.cache_snapshot_id}",
        shows=tuple(
            sorted(
                candidates_by_identity.values(),
                key=lambda candidate: (
                    _core.normalize_show_identity(candidate.title),
                    candidate.title,
                    candidate.identity.key,
                ),
            )
        ),
    )
    retry_provider = _CombinedSearchProvider(
        provider,
        (source_title, stripped_title),
        combined,
    )
    stripped_parses = tuple(
        replace(parse, series_hint=stripped_title, series_aliases=())
        for parse in parses
    )
    retried = _core.resolve_show_group_with_provider(
        source_key,
        stripped_parses,
        overrides,
        retry_provider,
    )
    if retried.status is not _core.ResolutionStatus.MATCHED or retried.show is None:
        return (
            _annotate(
                retried,
                (*reasons, "release-prefix-fallback:no-unique-stripped-match"),
            ),
            retry_provider,
        )

    observed = _core._observed_episode_evidence(
        stripped_parses,
        retried.show.numbering_mode,
    )
    if observed is None:
        return (
            _annotate(
                result,
                (*reasons, "release-prefix-fallback:catalog-evidence-unavailable"),
            ),
            retry_provider,
        )

    catalog = retry_provider.episode_catalog(retried.show.provider_identity)
    compatible, catalog_reasons = _core._catalog_compatibility_reasons(
        catalog, observed
    )
    if compatible is None:
        return (
            _annotate(
                result,
                (
                    *reasons,
                    *catalog_reasons,
                    "release-prefix-fallback:catalog-indeterminate",
                ),
                force_unresolved=True,
            ),
            retry_provider,
        )
    if not compatible:
        return (
            _annotate(
                result,
                (
                    *reasons,
                    *catalog_reasons,
                    "release-prefix-fallback:catalog-incompatible",
                ),
            ),
            retry_provider,
        )

    accepted = replace(
        retried,
        evidence=replace(
            retried.evidence,
            method=f"{retried.evidence.method}+release-prefix-fallback",
            reasons=(
                *reasons,
                *catalog_reasons,
                "release-prefix-fallback:catalog-confirmed",
                f"release-prefix-fallback-winner:{retried.show.provider_identity.key}",
                *retried.evidence.reasons,
            ),
        ),
    )
    return accepted, retry_provider
