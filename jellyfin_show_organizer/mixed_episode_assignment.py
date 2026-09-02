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
        if episode.airdate == source_date
        and (
            episode.season == 0
            or (episode.episode_type is not None and episode.episode_type != "regular")
        )
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
        if (
            reason := _pre_premiere_boundary_reason(source_date, show, catalog)
        ) is not None
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
    """Assign episodes and fail closed on provable pre-premiere regular claims.

    The existing mixed-family engine remains authoritative. A second provider-catalog
    pass is used only after that engine has produced a catalog-backed result. Sources
    with both explicit special context and a canonical date before the regular series
    boundary are removed from normal numbering, then the remaining sources are
    re-evaluated without those false claimants so legitimate regular assignments can
    recover from provider-episode collision protection.
    """

    source_group = tuple(
        sorted(
            sources,
            key=lambda source: (source.source_key.casefold(), source.source_key),
        )
    )
    original = _assign_episode_group_core(show, source_group, provider)
    if (
        show.numbering_mode not in _PARTITIONABLE_PRIMARY_MODES
        or original.catalog_request_key is None
        or show.provider != provider.provider_name
    ):
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
        for source in source_group
        if (
            assignment := _pre_premiere_assignment(source, show, catalog)
        ) is not None
    }
    if not guarded:
        return original

    remaining = tuple(
        source for source in source_group if source.source_key not in guarded
    )
    combined: list[SourceEpisodeAssignment] = list(guarded.values())
    request_keys = {catalog.request_key}
    if remaining:
        rerun = _assign_episode_group_core(show, remaining, provider)
        combined.extend(rerun.assignments)
        if rerun.catalog_request_key is not None:
            request_keys.add(rerun.catalog_request_key)

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
