from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from . import episode_assignment_strict as _strict
from . import mixed_episode_assignment as _mixed
from .episode_assignment_strict import (
    AssignmentStatus,
    EpisodeGroupAssignment,
    SourceEpisodeAssignment,
    SourceEpisodeInput,
)
from .models import CanonicalShow, NumberingMode
from .providers import MetadataProvider, TvmazeProviderAdapter
from .segment_counted_titles import normalize_episode_title
from .tvmaze_cache import JsonGetter, TvmazeCatalogCache


def _optional_leading_the_key(normalized_title: str) -> str:
    tokens = normalized_title.split()
    if len(tokens) > 1 and tokens[0] == "the":
        return " ".join(tokens[1:])
    return normalized_title


def _missing_segment_title_match(assignment: SourceEpisodeAssignment) -> bool:
    return assignment.status is AssignmentStatus.UNRESOLVED and any(
        reason.startswith("missing-segment-title-match:")
        for reason in assignment.evidence.reasons
    )


def _recover_optional_leading_the(
    show: CanonicalShow,
    sources: tuple[SourceEpisodeInput, ...],
    result: EpisodeGroupAssignment,
    provider: MetadataProvider,
) -> EpisodeGroupAssignment:
    by_source = {source.source_key: source for source in sources}
    eligible = tuple(
        assignment
        for assignment in result.assignments
        if _missing_segment_title_match(assignment)
        and (source := by_source.get(assignment.source_key)) is not None
        and not source.explicit_decision
        and source.parse.segment_hint is not None
        and source.parse.title_hint is not None
    )
    if not eligible or result.catalog_request_key is None:
        return result

    catalog = provider.episode_catalog(show.provider_identity)
    if (
        catalog.request_key != result.catalog_request_key
        or catalog.show_identity != show.provider_identity
        or not catalog.resolved
        or catalog.errors
        or not catalog.episodes
    ):
        return result

    recovered_by_source: dict[str, SourceEpisodeAssignment] = {}
    for assignment in eligible:
        source = by_source[assignment.source_key]
        normalized_title, _source_title_reasons = _strict._segment_source_title(
            source.parse.title_hint or "", source.source_key
        )
        article_key = _optional_leading_the_key(normalized_title)
        if not article_key:
            continue

        candidates = tuple(
            episode
            for episode in _strict._segment_catalog_candidates(source.parse, catalog)
            if (
                (candidate_title := normalize_episode_title(episode.title))
                != normalized_title
                and _optional_leading_the_key(candidate_title) == article_key
            )
        )
        base_reasons = tuple(
            reason
            for reason in assignment.evidence.reasons
            if not reason.startswith("missing-segment-title-match:")
        )
        if len(candidates) > 1:
            recovered_by_source[assignment.source_key] = replace(
                assignment,
                status=AssignmentStatus.SUSPICIOUS,
                evidence=replace(
                    assignment.evidence,
                    confidence=0.0,
                    reasons=(
                        *base_reasons,
                        f"ambiguous-segment-title-leading-article-match:{normalized_title}",
                    ),
                ),
            )
            continue
        if len(candidates) != 1:
            continue

        episode = candidates[0]
        recovered_by_source[assignment.source_key] = replace(
            assignment,
            status=AssignmentStatus.MATCHED,
            episodes=(episode,),
            evidence=replace(
                assignment.evidence,
                confidence=1.0,
                reasons=(
                    *base_reasons,
                    f"segment-title-leading-article-match:{normalized_title}",
                    _strict._episode_identity_reason(episode),
                ),
            ),
        )

    if not recovered_by_source:
        return result

    assignments = tuple(
        recovered_by_source.get(assignment.source_key, assignment)
        for assignment in result.assignments
    )
    if show.numbering_mode is NumberingMode.SEGMENT_TITLE:
        assignments = _strict._protect_segment_identity(sources, assignments)
    else:
        assignments = _strict._protect_provider_episode_identity(assignments)

    return replace(
        result,
        status=_strict._group_status(assignments),
        assignments=assignments,
    )


def assign_episode_group_with_provider(
    show: CanonicalShow,
    sources: Iterable[SourceEpisodeInput],
    provider: MetadataProvider,
) -> EpisodeGroupAssignment:
    """Assign episodes and recover one narrow explicit segment-title article gap."""

    source_group = tuple(
        sorted(
            sources,
            key=lambda source: (source.source_key.casefold(), source.source_key),
        )
    )
    result = _mixed.assign_episode_group_with_provider(show, source_group, provider)
    return _recover_optional_leading_the(show, source_group, result, provider)


def assign_episode_group(
    show: CanonicalShow,
    sources: Iterable[SourceEpisodeInput],
    cache: TvmazeCatalogCache,
    getter: JsonGetter,
) -> EpisodeGroupAssignment:
    """TVMaze compatibility wrapper around provider-neutral assignment."""

    return assign_episode_group_with_provider(
        show,
        sources,
        TvmazeProviderAdapter(cache, getter),
    )


def __getattr__(name: str):
    """Preserve access to strict internal helpers for compatibility."""

    return getattr(_strict, name)


__all__ = [
    "AssignmentStatus",
    "EpisodeGroupAssignment",
    "SourceEpisodeAssignment",
    "SourceEpisodeInput",
    "assign_episode_group",
    "assign_episode_group_with_provider",
]
