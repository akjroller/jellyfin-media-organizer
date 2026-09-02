from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import date

from .episode_assignment_strict import (
    AssignmentStatus,
    EpisodeGroupAssignment,
    SourceEpisodeAssignment,
    SourceEpisodeInput,
    _accessory_special_families_allowed,
    _catalog_diagnostic_reasons,
    _episode_identity_reason,
    _evidence_family,
    _expected_family,
    _group_status,
    _normalize_title,
    _protect_provider_episode_identity,
    assign_episode_group_with_provider as _assign_strict_group,
)
from .models import CanonicalShow, MatchEvidence, NumberingMode
from .providers import (
    MetadataProvider,
    ProviderEpisode,
    ProviderEpisodeCatalog,
    TvmazeProviderAdapter,
)
from .tvmaze_cache import JsonGetter, TvmazeCatalogCache

_FAMILY_MODE = {
    "aired": NumberingMode.AIRED,
    "absolute": NumberingMode.ABSOLUTE,
    "special": NumberingMode.SPECIAL,
    "date": NumberingMode.DATE,
    "segment": NumberingMode.SEGMENT_TITLE,
}
_PARTITIONABLE_PRIMARY_MODES = frozenset(
    {
        NumberingMode.AIRED,
        NumberingMode.ABSOLUTE,
        NumberingMode.PARENTHESIZED_ABSOLUTE,
    }
)
_SOURCE_DATE = re.compile(
    r"(?<!\d)(?P<year>(?:18|19|20|21)\d{2})[-._]"
    r"(?P<month>0[1-9]|1[0-2])[-._](?P<day>0[1-9]|[12]\d|3[01])(?!\d)"
)
_PRE_PREMIERE_CONTEXT = re.compile(
    r"(?i)(?<![a-z0-9])(?P<context>shorts?|pilots?|specials?|unaired)(?![a-z0-9])"
)


def _needs_partition(families: set[str], expected_family: str) -> bool:
    if "conflict" in families:
        return True
    return len(families) > 1 and not _accessory_special_families_allowed(
        families,
        expected_family,
    )


def _annotate_accessory(
    assignments: tuple[SourceEpisodeAssignment, ...],
    *,
    family: str,
    primary_mode: NumberingMode,
) -> tuple[SourceEpisodeAssignment, ...]:
    return tuple(
        replace(
            assignment,
            evidence=replace(
                assignment.evidence,
                reasons=(
                    f"mixed-numbering-family:{family}",
                    f"primary-numbering-mode:{primary_mode.value}",
                    *assignment.evidence.reasons,
                ),
            ),
        )
        for assignment in assignments
    )


def _source_dates(source_key: str) -> tuple[str, ...]:
    values: set[str] = set()
    for match in _SOURCE_DATE.finditer(source_key):
        candidate = f"{match.group('year')}-{match.group('month')}-{match.group('day')}"
        try:
            values.add(date.fromisoformat(candidate).isoformat())
        except ValueError:
            continue
    return tuple(sorted(values))


def _pre_premiere_contexts(source_key: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                match.group("context").casefold()
                for match in _PRE_PREMIERE_CONTEXT.finditer(source_key)
            }
        )
    )


def _has_pre_premiere_guard_signal(
    source: SourceEpisodeInput,
    show: CanonicalShow,
) -> bool:
    return (
        _evidence_family(source.parse, show.numbering_mode) in {"aired", "absolute"}
        and bool(_pre_premiere_contexts(source.source_key))
        and bool(_source_dates(source.source_key))
    )


def _is_non_regular_episode(episode: ProviderEpisode) -> bool:
    return episode.season == 0 or (
        episode.episode_type is not None and episode.episode_type != "regular"
    )


def _is_missing_special_number_assignment(
    source: SourceEpisodeInput,
    assignment: SourceEpisodeAssignment,
) -> bool:
    parse = source.parse
    return (
        assignment.status is AssignmentStatus.UNRESOLVED
        and parse.special_kind is not None
        and parse.special_episode is not None
        and f"missing-special-catalog-entry:{parse.special_episode}"
        in assignment.evidence.reasons
    )


def _has_special_fallback_signal(
    source: SourceEpisodeInput,
    assignment: SourceEpisodeAssignment,
) -> bool:
    return _is_missing_special_number_assignment(source, assignment) and (
        source.parse.title_hint is not None or bool(_source_dates(source.source_key))
    )


def _special_fallback_assignment(
    source: SourceEpisodeInput,
    assignment: SourceEpisodeAssignment,
    catalog: ProviderEpisodeCatalog,
) -> SourceEpisodeAssignment:
    if not _is_missing_special_number_assignment(source, assignment):
        return assignment

    parse = source.parse
    assert parse.special_kind is not None
    assert parse.special_episode is not None
    candidates = tuple(
        episode for episode in catalog.episodes if _is_non_regular_episode(episode)
    )
    reasons = [
        *assignment.evidence.reasons,
        "special-fallback:requested-number-missing",
        *_catalog_diagnostic_reasons(catalog),
    ]

    unique_matches: list[tuple[str, ProviderEpisode]] = []
    if parse.title_hint is not None:
        normalized_title = _normalize_title(parse.title_hint)
        title_matches = tuple(
            episode
            for episode in candidates
            if _normalize_title(episode.title) == normalized_title
        )
        if len(title_matches) > 1:
            return SourceEpisodeAssignment(
                source_key=source.source_key,
                status=AssignmentStatus.SUSPICIOUS,
                episodes=(),
                evidence=MatchEvidence(
                    method="special-catalog-fallback",
                    confidence=0.0,
                    reasons=(
                        *reasons,
                        f"special-fallback-title-ambiguous:{normalized_title}",
                    ),
                ),
            )
        if len(title_matches) == 1:
            unique_matches.append((f"title:{normalized_title}", title_matches[0]))
            reasons.append(f"special-fallback-title-match:{normalized_title}")

    source_dates = _source_dates(source.source_key)
    if len(source_dates) > 1:
        return SourceEpisodeAssignment(
            source_key=source.source_key,
            status=AssignmentStatus.SUSPICIOUS,
            episodes=(),
            evidence=MatchEvidence(
                method="special-catalog-fallback",
                confidence=0.0,
                reasons=(
                    *reasons,
                    "special-fallback-source-dates-ambiguous:" + ",".join(source_dates),
                ),
            ),
        )
    if len(source_dates) == 1:
        source_date = source_dates[0]
        date_matches = tuple(
            episode for episode in candidates if episode.airdate == source_date
        )
        if len(date_matches) > 1:
            return SourceEpisodeAssignment(
                source_key=source.source_key,
                status=AssignmentStatus.SUSPICIOUS,
                episodes=(),
                evidence=MatchEvidence(
                    method="special-catalog-fallback",
                    confidence=0.0,
                    reasons=(
                        *reasons,
                        f"special-fallback-date-ambiguous:{source_date}",
                    ),
                ),
            )
        if len(date_matches) == 1:
            unique_matches.append((f"date:{source_date}", date_matches[0]))
            reasons.append(f"special-fallback-date-match:{source_date}")

    unique_identities = {episode.identity for _method, episode in unique_matches}
    if len(unique_identities) > 1:
        return SourceEpisodeAssignment(
            source_key=source.source_key,
            status=AssignmentStatus.SUSPICIOUS,
            episodes=(),
            evidence=MatchEvidence(
                method="special-catalog-fallback",
                confidence=0.0,
                reasons=(
                    *reasons,
                    "special-fallback-evidence-conflict:"
                    + ",".join(method for method, _episode in unique_matches),
                ),
            ),
        )
    if not unique_matches:
        return assignment

    episode = unique_matches[0][1]
    if episode.number is None:
        return SourceEpisodeAssignment(
            source_key=source.source_key,
            status=AssignmentStatus.UNRESOLVED,
            episodes=(),
            evidence=MatchEvidence(
                method="special-catalog-fallback",
                confidence=0.0,
                reasons=(
                    *reasons,
                    "special-fallback-entry-missing-number",
                    _episode_identity_reason(episode),
                ),
            ),
        )

    return SourceEpisodeAssignment(
        source_key=source.source_key,
        status=AssignmentStatus.MATCHED,
        episodes=(episode,),
        evidence=MatchEvidence(
            method="special-catalog-fallback",
            confidence=1.0,
            reasons=(
                *reasons,
                f"special-kind:{parse.special_kind}",
                f"special-requested-number:{parse.special_episode}",
                f"special-fallback-match:{parse.special_kind.upper()}"
                f"{parse.special_episode}->S{episode.season:02d}E{episode.number:02d}",
                _episode_identity_reason(episode),
            ),
        ),
    )


def _first_regular_airdate(catalog: ProviderEpisodeCatalog) -> str | None:
    dates = tuple(
        sorted(
            episode.airdate
            for episode in catalog.episodes
            if episode.airdate is not None
            and episode.season > 0
            and episode.number is not None
            and (episode.episode_type is None or episode.episode_type == "regular")
        )
    )
    return dates[0] if dates else None


def _pre_premiere_boundary_reason(
    source_date: str,
    show: CanonicalShow,
    catalog: ProviderEpisodeCatalog,
) -> str | None:
    first_regular = _first_regular_airdate(catalog)
    if first_regular is not None:
        if source_date < first_regular:
            return f"pre-premiere-before-first-regular:{first_regular}"
        return None

    if show.year is not None and int(source_date[:4]) < show.year:
        return f"pre-premiere-before-show-year:{show.year}"
    return None


def _non_regular_on_date(
    catalog: ProviderEpisodeCatalog,
    source_date: str,
) -> tuple[ProviderEpisode, ...]:
    return tuple(
        episode
        for episode in catalog.episodes
        if episode.airdate == source_date and _is_non_regular_episode(episode)
    )


def _pre_premiere_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: ProviderEpisodeCatalog,
) -> SourceEpisodeAssignment | None:
    family = _evidence_family(source.parse, show.numbering_mode)
    if family not in {"aired", "absolute"}:
        return None

    contexts = _pre_premiere_contexts(source.source_key)
    if not contexts:
        return None

    source_dates = _source_dates(source.source_key)
    if not source_dates:
        return None

    boundary_reasons = tuple(
        reason
        for source_date in source_dates
        if (reason := _pre_premiere_boundary_reason(source_date, show, catalog))
        is not None
    )
    if not boundary_reasons:
        return None

    base_reasons = (
        f"primary-numbering-mode:{show.numbering_mode.value}",
        "pre-premiere-context:" + ",".join(contexts),
        f"catalog-request:{catalog.request_key}",
        *_catalog_diagnostic_reasons(catalog),
    )
    if len(source_dates) != 1:
        return SourceEpisodeAssignment(
            source_key=source.source_key,
            status=AssignmentStatus.SUSPICIOUS,
            episodes=(),
            evidence=MatchEvidence(
                method="pre-premiere-catalog",
                confidence=0.0,
                reasons=(
                    *base_reasons,
                    "ambiguous-pre-premiere-source-dates:" + ",".join(source_dates),
                    *boundary_reasons,
                ),
            ),
        )

    source_date = source_dates[0]
    boundary_reason = boundary_reasons[0]
    candidates = _non_regular_on_date(catalog, source_date)
    if not candidates:
        return SourceEpisodeAssignment(
            source_key=source.source_key,
            status=AssignmentStatus.UNRESOLVED,
            episodes=(),
            evidence=MatchEvidence(
                method="pre-premiere-catalog",
                confidence=0.0,
                reasons=(
                    *base_reasons,
                    f"pre-premiere-source-date:{source_date}",
                    boundary_reason,
                    f"missing-pre-premiere-catalog-entry:{source_date}",
                ),
            ),
        )

    selected = candidates
    title_reason: str | None = None
    if len(candidates) > 1 and source.parse.title_hint is not None:
        normalized_title = _normalize_title(source.parse.title_hint)
        title_matches = tuple(
            episode
            for episode in candidates
            if _normalize_title(episode.title) == normalized_title
        )
        if len(title_matches) == 1:
            selected = title_matches
            title_reason = f"pre-premiere-title-match:{normalized_title}"

    if len(selected) != 1:
        return SourceEpisodeAssignment(
            source_key=source.source_key,
            status=AssignmentStatus.SUSPICIOUS,
            episodes=(),
            evidence=MatchEvidence(
                method="pre-premiere-catalog",
                confidence=0.0,
                reasons=(
                    *base_reasons,
                    f"pre-premiere-source-date:{source_date}",
                    boundary_reason,
                    f"ambiguous-pre-premiere-catalog-entry:{source_date}",
                ),
            ),
        )

    episode = selected[0]
    if episode.number is None:
        return SourceEpisodeAssignment(
            source_key=source.source_key,
            status=AssignmentStatus.UNRESOLVED,
            episodes=(),
            evidence=MatchEvidence(
                method="pre-premiere-catalog",
                confidence=0.0,
                reasons=(
                    *base_reasons,
                    f"pre-premiere-source-date:{source_date}",
                    boundary_reason,
                    f"pre-premiere-catalog-entry-missing-number:{source_date}",
                    _episode_identity_reason(episode),
                ),
            ),
        )

    reasons = [
        *base_reasons,
        f"pre-premiere-source-date:{source_date}",
        boundary_reason,
    ]
    if title_reason is not None:
        reasons.append(title_reason)
    reasons.extend(
        (
            f"pre-premiere-catalog-match:{source_date}->"
            f"S{episode.season:02d}E{episode.number:02d}",
            _episode_identity_reason(episode),
        )
    )
    return SourceEpisodeAssignment(
        source_key=source.source_key,
        status=AssignmentStatus.MATCHED,
        episodes=(episode,),
        evidence=MatchEvidence(
            method="pre-premiere-catalog",
            confidence=1.0,
            reasons=tuple(reasons),
        ),
    )


def _assign_episode_group_core(
    show: CanonicalShow,
    sources: Iterable[SourceEpisodeInput],
    provider: MetadataProvider,
) -> EpisodeGroupAssignment:
    """Assign a resolved show while isolating independent numbering families."""

    source_group = tuple(
        sorted(
            sources,
            key=lambda source: (source.source_key.casefold(), source.source_key),
        )
    )
    if not source_group:
        raise ValueError("episode assignment requires at least one source")

    if show.numbering_mode not in _PARTITIONABLE_PRIMARY_MODES:
        return _assign_strict_group(show, source_group, provider)

    expected_family = _expected_family(show.numbering_mode)
    family_by_source = {
        source.source_key: _evidence_family(source.parse, show.numbering_mode)
        for source in source_group
    }
    families = {family for family in family_by_source.values() if family != "none"}

    if not _needs_partition(families, expected_family):
        return _assign_strict_group(show, source_group, provider)

    # A show-wide numbering policy is still authoritative when none of the sources
    # actually carries its expected family. Partitioning must not turn a policy
    # conflict into an implicit override.
    if expected_family not in families:
        return _assign_strict_group(show, source_group, provider)

    grouped: dict[str, list[SourceEpisodeInput]] = {}
    for source in source_group:
        family = family_by_source[source.source_key]
        grouped.setdefault(family, []).append(source)

    combined: list[SourceEpisodeAssignment] = []
    request_keys: set[str] = set()
    for family in sorted(grouped):
        subgroup = tuple(grouped[family])
        if family in {expected_family, "none", "conflict"}:
            subgroup_show = show
        else:
            mode = _FAMILY_MODE.get(family)
            if mode is None:
                subgroup_show = show
            else:
                subgroup_show = replace(show, numbering_mode=mode)

        result = _assign_strict_group(subgroup_show, subgroup, provider)
        if result.catalog_request_key is not None:
            request_keys.add(result.catalog_request_key)
        assignments = result.assignments
        if family not in {expected_family, "none", "conflict"}:
            assignments = _annotate_accessory(
                assignments,
                family=family,
                primary_mode=show.numbering_mode,
            )
        combined.extend(assignments)

    ordered = tuple(
        sorted(
            combined,
            key=lambda assignment: (
                assignment.source_key.casefold(),
                assignment.source_key,
            ),
        )
    )
    ordered = _protect_provider_episode_identity(ordered)
    request_key = next(iter(request_keys)) if len(request_keys) == 1 else None
    return EpisodeGroupAssignment(
        show=show,
        status=_group_status(ordered),
        assignments=ordered,
        catalog_request_key=request_key,
    )


def assign_episode_group_with_provider(
    show: CanonicalShow,
    sources: Iterable[SourceEpisodeInput],
    provider: MetadataProvider,
) -> EpisodeGroupAssignment:
    """Assign episodes and run narrowly-scoped provider-evidence recovery passes."""

    source_group = tuple(
        sorted(
            sources,
            key=lambda source: (source.source_key.casefold(), source.source_key),
        )
    )
    original = _assign_episode_group_core(show, source_group, provider)
    if original.catalog_request_key is None or show.provider != provider.provider_name:
        return original

    original_by_source = {
        assignment.source_key: assignment for assignment in original.assignments
    }
    potential_special_sources = tuple(
        source
        for source in source_group
        if (assignment := original_by_source.get(source.source_key)) is not None
        and _has_special_fallback_signal(source, assignment)
    )
    potential_guard_sources = (
        tuple(
            source
            for source in source_group
            if _has_pre_premiere_guard_signal(source, show)
        )
        if show.numbering_mode in _PARTITIONABLE_PRIMARY_MODES
        else ()
    )
    if not potential_special_sources and not potential_guard_sources:
        return original

    catalog = provider.episode_catalog(show.provider_identity)
    if (
        catalog.request_key != original.catalog_request_key
        or catalog.show_identity != show.provider_identity
        or not catalog.resolved
        or catalog.errors
        or not catalog.episodes
    ):
        return original

    guarded = {
        source.source_key: assignment
        for source in potential_guard_sources
        if (assignment := _pre_premiere_assignment(source, show, catalog)) is not None
    }
    request_keys = {catalog.request_key}
    if guarded:
        remaining = tuple(
            source for source in source_group if source.source_key not in guarded
        )
        base_assignments = dict(guarded)
        if remaining:
            rerun = _assign_episode_group_core(show, remaining, provider)
            base_assignments.update(
                {assignment.source_key: assignment for assignment in rerun.assignments}
            )
            if rerun.catalog_request_key is not None:
                request_keys.add(rerun.catalog_request_key)
    else:
        base_assignments = dict(original_by_source)

    for source in potential_special_sources:
        assignment = base_assignments.get(source.source_key)
        if assignment is None:
            continue
        base_assignments[source.source_key] = _special_fallback_assignment(
            source,
            assignment,
            catalog,
        )

    ordered = tuple(
        sorted(
            base_assignments.values(),
            key=lambda assignment: (
                assignment.source_key.casefold(),
                assignment.source_key,
            ),
        )
    )
    ordered = _protect_provider_episode_identity(ordered)
    request_key = next(iter(request_keys)) if len(request_keys) == 1 else None
    return EpisodeGroupAssignment(
        show=show,
        status=_group_status(ordered),
        assignments=ordered,
        catalog_request_key=request_key,
    )


def assign_episode_group(
    show: CanonicalShow,
    sources: Iterable[SourceEpisodeInput],
    cache: TvmazeCatalogCache,
    getter: JsonGetter,
) -> EpisodeGroupAssignment:
    """TVMaze compatibility wrapper around mixed-family assignment."""

    return assign_episode_group_with_provider(
        show,
        sources,
        TvmazeProviderAdapter(cache, getter),
    )
