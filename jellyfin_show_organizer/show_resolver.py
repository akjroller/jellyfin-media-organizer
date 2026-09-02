from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from . import _show_resolver_core as _core
from .models import CandidateEvidence, ParseResult, ProviderIdentity
from .overrides import OverrideCatalog
from .parenthetical_aliases import parenthetical_show_aliases
from .provider_aliases import TvmazeAliasProviderAdapter
from .providers import MetadataProvider, ProviderSearchSnapshot, ProviderShow
from .show_structural_evidence import token_merge_queries
from .tvmaze_cache import JsonGetter, TvmazeCatalogCache

ResolutionStatus = _core.ResolutionStatus
ShowResolution = _core.ShowResolution
normalize_show_identity = _core.normalize_show_identity

_STRUCTURAL_YEAR_RANGE = re.compile(
    r"(?<!\d)((?:18|19|20)\d{2})\s*[-\u2013\u2014]\s*((?:18|19|20)\d{2})(?!\d)"
)
_STRUCTURAL_SINGLE_YEAR = re.compile(r"[\[(]\s*((?:18|19|20)\d{2})\s*[\])]")


def _structural_year_span(source_key: str) -> tuple[int, int] | None:
    ranges = {
        (int(match.group(1)), int(match.group(2)))
        for match in _STRUCTURAL_YEAR_RANGE.finditer(source_key)
        if int(match.group(1)) <= int(match.group(2))
    }
    if len(ranges) > 1:
        return None
    if ranges:
        return next(iter(ranges))

    years = {
        int(match.group(1)) for match in _STRUCTURAL_SINGLE_YEAR.finditer(source_key)
    }
    if len(years) != 1:
        return None
    year = next(iter(years))
    return year, year


def _span_reason(span: tuple[int, int]) -> str:
    start, end = span
    if start == end:
        return f"structural-source-year:{start}"
    return f"structural-source-year-range:{start}-{end}"


def _top_contenders(
    ranked: tuple[CandidateEvidence, ...],
) -> tuple[CandidateEvidence, ...]:
    if not ranked:
        return ()
    top_score = ranked[0].score
    return tuple(
        candidate
        for candidate in ranked
        if top_score - candidate.score < _core._MINIMUM_MATCH_GAP
    )


def _structural_candidate_reasons(
    candidate: CandidateEvidence,
    provider_by_identity: Mapping[ProviderIdentity, ProviderShow],
    span: tuple[int, int],
    span_reason: str,
) -> tuple[str, ...]:
    show = provider_by_identity.get(candidate.provider_identity)
    if show is None or show.year is None:
        return ()
    start, end = span
    return (
        span_reason,
        f"provider-year:{show.year}",
        f"structural-year-compatible:{str(start <= show.year <= end).casefold()}",
    )


class _MergedSearchProvider:
    """Serve one augmented search snapshot while delegating all other metadata."""

    def __init__(
        self,
        provider: MetadataProvider,
        search_title: str | tuple[str, ...],
        snapshot: ProviderSearchSnapshot,
    ) -> None:
        self._provider = provider
        self._search_titles = frozenset(
            (search_title,) if isinstance(search_title, str) else search_title
        )
        self._snapshot = snapshot

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        if title in self._search_titles:
            return self._snapshot
        return self._provider.search_shows(title)

    def episode_catalog(self, show_identity: ProviderIdentity):
        return self._provider.episode_catalog(show_identity)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


def _parenthetical_alias_pair(
    source_key: str,
    parse_group: tuple[ParseResult, ...],
) -> tuple[str, ...]:
    observed: list[tuple[str, ...]] = []
    source_aliases = parenthetical_show_aliases(source_key)
    if source_aliases:
        observed.append(source_aliases)
    observed.extend(
        parse.series_aliases for parse in parse_group if parse.series_aliases
    )
    if not observed:
        return ()

    normalized_pairs = {
        tuple(sorted(normalize_show_identity(alias) for alias in pair))
        for pair in observed
    }
    if len(normalized_pairs) != 1:
        return ()

    normalized_pair = next(iter(normalized_pairs))
    display_by_identity: dict[str, str] = {}
    for pair in observed:
        for alias in pair:
            identity = normalize_show_identity(alias)
            previous = display_by_identity.get(identity)
            if previous is None or (alias.casefold(), alias) < (
                previous.casefold(),
                previous,
            ):
                display_by_identity[identity] = alias
    return tuple(display_by_identity[identity] for identity in normalized_pair)


def _parenthetical_retry_failure(
    result: ShowResolution,
    reasons: tuple[str, ...],
    *,
    force_unresolved: bool = False,
) -> ShowResolution:
    return replace(
        result,
        status=(ResolutionStatus.UNRESOLVED if force_unresolved else result.status),
        show=None,
        evidence=replace(
            result.evidence,
            method=f"{result.evidence.method}+parenthetical-alias-search",
            confidence=(0.0 if force_unresolved else result.evidence.confidence),
            reasons=(*result.evidence.reasons, *reasons),
        ),
    )


def _parenthetical_alias_resolution(
    source_key: str,
    parse_group: tuple[ParseResult, ...],
    overrides: OverrideCatalog,
    provider: MetadataProvider,
    result: ShowResolution,
) -> tuple[ShowResolution, MetadataProvider] | None:
    """Retry a non-match using one conservative parenthetical alias pair."""

    if result.status is ResolutionStatus.MATCHED or result.show is not None:
        return None

    titles = _core._source_titles(parse_group)
    override_matches = _core._matching_overrides(source_key, titles, overrides)
    if override_matches or _core._explicit_identities(parse_group, None):
        return None

    aliases = _parenthetical_alias_pair(source_key, parse_group)
    if len(aliases) != 2:
        return None

    source_title = _core._representative_title(titles)
    if source_title is None:
        return None

    query_titles = [source_title]
    seen_queries = {normalize_show_identity(source_title)}
    for alias in aliases:
        identity = normalize_show_identity(alias)
        if identity and identity not in seen_queries:
            seen_queries.add(identity)
            query_titles.append(alias)
    if len(query_titles) < 2:
        return None

    reasons: list[str] = ["parenthetical-alias-search:attempted"]
    candidates_by_identity: dict[ProviderIdentity, ProviderShow] = {}
    snapshots: list[ProviderSearchSnapshot] = []
    for index, query in enumerate(query_titles):
        snapshot = provider.search_shows(query)
        snapshots.append(snapshot)
        role = "primary" if index == 0 else "alias"
        reasons.extend(
            (
                f"parenthetical-alias-search-{role}-query:"
                f"{normalize_show_identity(query)}",
                f"parenthetical-alias-search-request:{snapshot.request_key}",
            )
        )
        if not snapshot.resolved:
            failed = _parenthetical_retry_failure(
                result,
                (
                    *reasons,
                    "parenthetical-alias-search:indeterminate",
                    snapshot.unresolved_reason or "provider-search-unresolved",
                ),
                force_unresolved=True,
            )
            return failed, provider
        for candidate in snapshot.shows:
            previous = candidates_by_identity.get(candidate.identity)
            if previous is not None and previous != candidate:
                failed = _parenthetical_retry_failure(
                    result,
                    (
                        *reasons,
                        "parenthetical-alias-search:conflicting-candidate-metadata",
                        f"provider-identity:{candidate.identity.key}",
                    ),
                    force_unresolved=True,
                )
                return failed, provider
            candidates_by_identity[candidate.identity] = candidate

    reasons.append("parenthetical-alias-search:complete")
    if not candidates_by_identity:
        return _parenthetical_retry_failure(result, tuple(reasons)), provider

    combined = ProviderSearchSnapshot(
        provider=snapshots[0].provider,
        request_key="|".join(snapshot.request_key for snapshot in snapshots),
        cache_snapshot_id="|".join(
            snapshot.cache_snapshot_id for snapshot in snapshots
        ),
        shows=tuple(
            sorted(
                candidates_by_identity.values(),
                key=lambda candidate: (
                    normalize_show_identity(candidate.title),
                    candidate.title,
                    candidate.identity.key,
                ),
            )
        ),
    )
    retry_provider = _MergedSearchProvider(provider, tuple(query_titles), combined)

    alias_results: list[ShowResolution] = []
    for alias in aliases:
        alias_parses = tuple(
            replace(parse, series_hint=alias, series_aliases=())
            for parse in parse_group
        )
        alias_results.append(
            _core.resolve_show_group_with_provider(
                source_key,
                alias_parses,
                overrides,
                retry_provider,
            )
        )

    matched = tuple(
        alias_result
        for alias_result in alias_results
        if alias_result.status is ResolutionStatus.MATCHED
        and alias_result.show is not None
    )
    matched_identities = {
        alias_result.show.provider_identity
        for alias_result in matched
        if alias_result.show is not None
    }
    has_suspicious = any(
        alias_result.status is ResolutionStatus.SUSPICIOUS
        for alias_result in alias_results
    )

    if len(matched_identities) == 1 and matched and not has_suspicious:
        winner = matched_identities.pop()
        chosen = next(
            alias_result
            for alias_result in matched
            if alias_result.show is not None
            and alias_result.show.provider_identity == winner
        )
        chosen = replace(
            chosen,
            evidence=replace(
                chosen.evidence,
                method=f"{chosen.evidence.method}+parenthetical-alias-search",
                reasons=(
                    *reasons,
                    f"parenthetical-alias-search-winner:{winner.key}",
                    *chosen.evidence.reasons,
                ),
            ),
        )
        return chosen, retry_provider

    if len(matched_identities) > 1 or has_suspicious:
        failed = _parenthetical_retry_failure(
            result,
            (*reasons, "parenthetical-alias-search:conflicting-results"),
        )
        return failed, retry_provider

    failed = _parenthetical_retry_failure(
        result,
        (*reasons, "parenthetical-alias-search:no-unique-match"),
    )
    return failed, retry_provider


def _token_merge_retry_failure(
    result: ShowResolution,
    reasons: tuple[str, ...],
) -> ShowResolution:
    return replace(
        result,
        evidence=replace(
            result.evidence,
            method=f"{result.evidence.method}+weak-token-merge-retry",
            reasons=(*result.evidence.reasons, *reasons),
        ),
    )


def _weak_token_merge_resolution(
    source_key: str,
    parse_group: tuple[ParseResult, ...],
    overrides: OverrideCatalog,
    provider: MetadataProvider,
    result: ShowResolution,
) -> tuple[ShowResolution, MetadataProvider] | None:
    """Retry deterministic token compaction only after weak exact-search evidence."""

    if result.status is not ResolutionStatus.UNRESOLVED or result.show is not None:
        return None
    if "provider-evidence-below-threshold" not in result.evidence.reasons:
        return None
    if "provider-search-token-merge:attempted" in result.evidence.reasons:
        return None

    titles = _core._source_titles(parse_group)
    source_title = _core._representative_title(titles)
    override_matches = _core._matching_overrides(source_key, titles, overrides)
    if len(override_matches) > 1:
        return None
    override = override_matches[0] if override_matches else None

    search_title = source_title
    if override is not None and override.preferred_title:
        search_title = override.preferred_title
    if search_title is None:
        return None

    merge_titles = token_merge_queries(search_title)
    if len(merge_titles) != 1:
        return None
    merge_title = merge_titles[0]
    retry_reasons: tuple[str, ...] = (
        "provider-search-token-merge:attempted",
        "provider-search-token-merge-trigger:weak-exact-candidates",
        f"provider-search-token-merge-query:{normalize_show_identity(merge_title)}",
    )

    exact = provider.search_shows(search_title)
    if not exact.resolved:
        failed = _token_merge_retry_failure(
            result,
            (
                *retry_reasons,
                "provider-search-token-merge:exact-search-indeterminate",
                exact.unresolved_reason or "provider-search-unresolved",
            ),
        )
        return failed, provider
    if not exact.shows:
        failed = _token_merge_retry_failure(
            result,
            (
                *retry_reasons,
                "provider-search-token-merge:initial-candidates-not-reproducible",
            ),
        )
        return failed, provider

    merged = provider.search_shows(merge_title)
    retry_reasons = (
        *retry_reasons,
        f"provider-search-token-merge-request:{merged.request_key}",
        f"provider-search-token-merge-snapshot:{merged.cache_snapshot_id}",
    )
    if not merged.resolved:
        failed = _token_merge_retry_failure(
            result,
            (
                *retry_reasons,
                "provider-search-token-merge:indeterminate",
                merged.unresolved_reason or "provider-search-unresolved",
            ),
        )
        return failed, provider

    candidates_by_identity: dict[ProviderIdentity, ProviderShow] = {
        candidate.identity: candidate for candidate in exact.shows
    }
    new_identity = False
    for candidate in merged.shows:
        previous = candidates_by_identity.get(candidate.identity)
        if previous is not None and previous != candidate:
            failed = _token_merge_retry_failure(
                result,
                (
                    *retry_reasons,
                    "provider-search-token-merge:conflicting-candidate-metadata",
                    f"provider-identity:{candidate.identity.key}",
                ),
            )
            return failed, provider
        if previous is None:
            new_identity = True
        candidates_by_identity[candidate.identity] = candidate

    complete_reasons = (*retry_reasons, "provider-search-token-merge:complete")
    if not new_identity:
        return _token_merge_retry_failure(result, complete_reasons), provider

    combined = ProviderSearchSnapshot(
        provider=exact.provider,
        request_key=f"{exact.request_key}|{merged.request_key}",
        cache_snapshot_id=f"{exact.cache_snapshot_id}|{merged.cache_snapshot_id}",
        shows=tuple(
            sorted(
                candidates_by_identity.values(),
                key=lambda candidate: (
                    normalize_show_identity(candidate.title),
                    candidate.title,
                    candidate.identity.key,
                ),
            )
        ),
    )
    retry_provider = _MergedSearchProvider(provider, search_title, combined)
    retried = _core.resolve_show_group_with_provider(
        source_key,
        parse_group,
        overrides,
        retry_provider,
    )
    retried = replace(
        retried,
        evidence=replace(
            retried.evidence,
            method=f"{retried.evidence.method}+weak-token-merge-retry",
            reasons=(*complete_reasons, *retried.evidence.reasons),
        ),
    )
    return retried, retry_provider


def _structural_year_resolution(
    source_key: str,
    parse_group: tuple[ParseResult, ...],
    overrides: OverrideCatalog,
    provider: MetadataProvider,
    result: ShowResolution,
) -> ShowResolution | None:
    span = _structural_year_span(source_key)
    if span is None:
        return None
    if result.status is not ResolutionStatus.SUSPICIOUS or result.show is not None:
        return None
    if "ambiguous-top-candidates" not in result.evidence.reasons:
        return None

    year_hint, year_consistent = _core._consistent_year(parse_group)
    if not year_consistent or year_hint is not None:
        return None

    titles = _core._source_titles(parse_group)
    source_title = _core._representative_title(titles)
    override_matches = _core._matching_overrides(source_key, titles, overrides)
    if len(override_matches) > 1:
        return None
    override = override_matches[0] if override_matches else None
    if override is not None and (
        override.year is not None or override.provider_identity is not None
    ):
        return None

    ranked = result.evidence.candidates
    if not ranked or ranked[0].score < _core._MATCH_THRESHOLD:
        return None
    contenders = _top_contenders(ranked)
    if len(contenders) < 2:
        return None
    contender_titles = {
        normalize_show_identity(candidate.title) for candidate in contenders
    }
    if len(contender_titles) != 1:
        return None

    search_title = source_title
    if override is not None and override.preferred_title:
        search_title = override.preferred_title
    if search_title is None:
        return None

    snapshot = provider.search_shows(search_title)
    if not snapshot.resolved:
        return None
    provider_by_identity = {show.identity: show for show in snapshot.shows}
    contender_shows: list[tuple[ProviderShow, int]] = []
    for contender in contenders:
        show = provider_by_identity.get(contender.provider_identity)
        if show is None or show.year is None:
            return None
        contender_shows.append((show, show.year))

    start, end = span
    compatible = tuple(show for show, year in contender_shows if start <= year <= end)
    if len(compatible) != 1:
        return None
    winner_show = compatible[0]

    span_reason = _span_reason(span)
    enriched = tuple(
        replace(
            candidate,
            reasons=(
                *candidate.reasons,
                *_structural_candidate_reasons(
                    candidate,
                    provider_by_identity,
                    span,
                    span_reason,
                ),
            ),
        )
        for candidate in ranked
    )
    winner_first = tuple(
        sorted(
            enriched,
            key=lambda candidate: (
                candidate.provider_identity != winner_show.identity,
                -candidate.score,
                normalize_show_identity(candidate.title),
                candidate.provider_identity.key,
            ),
        )
    )
    winner_evidence = next(
        candidate
        for candidate in winner_first
        if candidate.provider_identity == winner_show.identity
    )
    title = _core._preferred_title(override, source_title, winner_show.title)
    if title is None:
        return None

    prior_reasons = tuple(
        reason
        for reason in result.evidence.reasons
        if reason != "ambiguous-top-candidates"
    )
    return _core._resolved_show_result(
        source_key=source_key,
        parse_group=parse_group,
        override=override,
        provider=provider,
        provider_identity=winner_show.identity,
        title=title,
        year=winner_show.year,
        method=f"{result.evidence.method}+structural-year-tiebreak",
        confidence=winner_evidence.score,
        reasons=(
            *prior_reasons,
            span_reason,
            "structural-year-tiebreak:unique-compatible-candidate",
            f"structural-year-tiebreak-winner:{winner_show.identity.key}",
        ),
        candidates=winner_first,
    )


def resolve_show_group_with_provider(
    source_key: str,
    parses: Iterable[ParseResult],
    overrides: OverrideCatalog,
    provider: MetadataProvider,
) -> ShowResolution:
    parse_group = tuple(parses)
    result = _core.resolve_show_group_with_provider(
        source_key,
        parse_group,
        overrides,
        provider,
    )
    active_provider: MetadataProvider = provider

    parenthetical = _parenthetical_alias_resolution(
        source_key,
        parse_group,
        overrides,
        provider,
        result,
    )
    if parenthetical is not None:
        result, active_provider = parenthetical

    retry = _weak_token_merge_resolution(
        source_key,
        parse_group,
        overrides,
        active_provider,
        result,
    )
    if retry is not None:
        result, active_provider = retry

    if _structural_year_span(source_key) is None:
        return result
    structural = _structural_year_resolution(
        source_key,
        parse_group,
        overrides,
        active_provider,
        result,
    )
    return structural if structural is not None else result


def resolve_show_group(
    source_key: str,
    parses: Iterable[ParseResult],
    overrides: OverrideCatalog,
    cache: TvmazeCatalogCache,
    getter: JsonGetter,
) -> ShowResolution:
    return resolve_show_group_with_provider(
        source_key,
        parses,
        overrides,
        TvmazeAliasProviderAdapter(cache, getter),
    )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)
