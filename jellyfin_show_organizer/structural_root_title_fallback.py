from __future__ import annotations

import re
import unicodedata
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
from .show_structural_evidence import structural_title_score

_MIN_CATALOG_OBSERVATIONS = 2


def structural_root_title(source_key: str, source_title: str) -> str | None:
    """Return one root prefix proven to expand a parsed title initialism."""

    normalized_source = _core.normalize_show_identity(source_title)
    if not normalized_source:
        return None

    tokens = re.findall(
        r"\w+",
        unicodedata.normalize("NFKC", source_key),
        flags=re.UNICODE,
    )
    if len(tokens) < 2:
        return None

    candidates: dict[str, str] = {}
    for end in range(len(tokens), 1, -1):
        prefix = " ".join(tokens[:end])
        identity = _core.normalize_show_identity(prefix)
        if not identity or identity == normalized_source:
            continue
        score, reasons = structural_title_score((normalized_source,), prefix)
        if score is None or "token-initialism-equivalent" not in reasons:
            continue
        previous = candidates.get(identity)
        if previous is None or (prefix.casefold(), prefix) < (
            previous.casefold(),
            previous,
        ):
            candidates[identity] = prefix

    if len(candidates) != 1:
        return None
    return next(iter(candidates.values()))


class _CombinedSearchProvider:
    """Replay one deterministic candidate union for original and root titles."""

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
            method=f"{result.evidence.method}+structural-root-title-fallback",
            confidence=(0.0 if force_unresolved else result.evidence.confidence),
            reasons=(*result.evidence.reasons, *reasons),
        ),
    )


def resolve_structural_root_title_fallback(
    source_key: str,
    parses: tuple[ParseResult, ...],
    overrides: OverrideCatalog,
    provider: MetadataProvider,
    result: _core.ShowResolution,
) -> tuple[_core.ShowResolution, MetadataProvider] | None:
    """Retry one structural root title and require multi-episode catalog proof."""

    if (
        result.status is not _core.ResolutionStatus.UNRESOLVED
        or result.show is not None
        or "provider-evidence-below-threshold" not in result.evidence.reasons
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
    root_title = structural_root_title(source_key, source_title)
    if root_title is None:
        return None

    reasons: list[str] = [
        "structural-root-title-fallback:attempted",
        f"structural-root-title-source:{_core.normalize_show_identity(source_title)}",
        f"structural-root-title-query:{_core.normalize_show_identity(root_title)}",
        "structural-root-title-proof:token-initialism-equivalent",
    ]

    original = provider.search_shows(source_title)
    reasons.append(f"structural-root-title-original-request:{original.request_key}")
    if not original.resolved:
        return (
            _annotate(
                result,
                (
                    *reasons,
                    "structural-root-title-fallback:original-search-indeterminate",
                    original.unresolved_reason or "provider-search-unresolved",
                ),
                force_unresolved=True,
            ),
            provider,
        )

    root = provider.search_shows(root_title)
    reasons.append(f"structural-root-title-root-request:{root.request_key}")
    if not root.resolved:
        return (
            _annotate(
                result,
                (
                    *reasons,
                    "structural-root-title-fallback:root-search-indeterminate",
                    root.unresolved_reason or "provider-search-unresolved",
                ),
                force_unresolved=True,
            ),
            provider,
        )

    candidates_by_identity: dict[ProviderIdentity, ProviderShow] = {}
    for snapshot in (original, root):
        for candidate in snapshot.shows:
            previous = candidates_by_identity.get(candidate.identity)
            if previous is not None and previous != candidate:
                return (
                    _annotate(
                        result,
                        (
                            *reasons,
                            "structural-root-title-fallback:conflicting-candidate-metadata",
                            f"provider-identity:{candidate.identity.key}",
                        ),
                        force_unresolved=True,
                    ),
                    provider,
                )
            candidates_by_identity[candidate.identity] = candidate

    reasons.append("structural-root-title-fallback:search-complete")
    if not root.shows:
        return _annotate(result, tuple(reasons)), provider

    combined = ProviderSearchSnapshot(
        provider=root.provider,
        request_key=f"{original.request_key}|{root.request_key}",
        cache_snapshot_id=f"{original.cache_snapshot_id}|{root.cache_snapshot_id}",
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
        (source_title, root_title),
        combined,
    )
    root_parses = tuple(
        replace(parse, series_hint=root_title, series_aliases=()) for parse in parses
    )
    retried = _core.resolve_show_group_with_provider(
        source_key,
        root_parses,
        overrides,
        retry_provider,
    )
    if retried.status is not _core.ResolutionStatus.MATCHED or retried.show is None:
        return (
            _annotate(
                retried,
                (*reasons, "structural-root-title-fallback:no-unique-root-match"),
            ),
            retry_provider,
        )

    observed = _core._observed_episode_evidence(
        root_parses,
        retried.show.numbering_mode,
    )
    if observed is None or len(observed.values) < _MIN_CATALOG_OBSERVATIONS:
        return (
            _annotate(
                result,
                (
                    *reasons,
                    "structural-root-title-fallback:insufficient-catalog-evidence",
                ),
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
                    "structural-root-title-fallback:catalog-indeterminate",
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
                    "structural-root-title-fallback:catalog-incompatible",
                ),
            ),
            retry_provider,
        )

    accepted = replace(
        retried,
        evidence=replace(
            retried.evidence,
            method=f"{retried.evidence.method}+structural-root-title-fallback",
            reasons=(
                *reasons,
                *catalog_reasons,
                "structural-root-title-fallback:catalog-confirmed",
                f"structural-root-title-fallback-winner:{retried.show.provider_identity.key}",
                *retried.evidence.reasons,
            ),
        ),
    )
    return accepted, retry_provider
