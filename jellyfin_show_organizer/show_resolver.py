from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from . import _show_resolver_core as _core
from .models import CandidateEvidence, ParseResult
from .overrides import OverrideCatalog
from .provider_aliases import TvmazeAliasProviderAdapter
from .providers import MetadataProvider, ProviderShow
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
    contender_shows: list[ProviderShow] = []
    for contender in contenders:
        show = provider_by_identity.get(contender.provider_identity)
        if show is None or show.year is None:
            return None
        contender_shows.append(show)

    start, end = span
    compatible = tuple(show for show in contender_shows if start <= show.year <= end)
    if len(compatible) != 1:
        return None
    winner_show = compatible[0]

    span_reason = _span_reason(span)
    enriched = tuple(
        replace(
            candidate,
            reasons=(
                *candidate.reasons,
                *(
                    (
                        span_reason,
                        f"provider-year:{provider_by_identity[candidate.provider_identity].year}",
                        "structural-year-compatible:"
                        f"{str(start <= provider_by_identity[candidate.provider_identity].year <= end).casefold()}",
                    )
                    if candidate.provider_identity in provider_by_identity
                    and provider_by_identity[candidate.provider_identity].year
                    is not None
                    else ()
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
    if _structural_year_span(source_key) is None:
        return result
    structural = _structural_year_resolution(
        source_key,
        parse_group,
        overrides,
        provider,
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
