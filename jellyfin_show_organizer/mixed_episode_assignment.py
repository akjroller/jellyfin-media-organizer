from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from .episode_assignment_strict import (
    EpisodeGroupAssignment,
    SourceEpisodeAssignment,
    SourceEpisodeInput,
    _accessory_special_families_allowed,
    _evidence_family,
    _expected_family,
    _group_status,
    _protect_provider_episode_identity,
    assign_episode_group_with_provider as _assign_strict_group,
)
from .models import CanonicalShow, NumberingMode
from .providers import MetadataProvider, TvmazeProviderAdapter
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


def assign_episode_group_with_provider(
    show: CanonicalShow,
    sources: Iterable[SourceEpisodeInput],
    provider: MetadataProvider,
) -> EpisodeGroupAssignment:
    """Assign a resolved show while isolating independent numbering families.

    The strict assignment engine remains authoritative for every individual family.
    Partitioning is used only when an aired/absolute primary show contains the
    selected primary family plus independent alternate evidence that the strict
    group-level guard would otherwise reject wholesale. Explicit date, special, and
    segment-title policies remain strict group-wide contracts. Each partitioned
    family is validated against the same provider show catalog, and cross-family
    provider-episode collisions remain fail-closed.
    """

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
