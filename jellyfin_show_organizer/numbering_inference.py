from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import NumberingMode, ParseResult, ProviderIdentity
from .providers import ProviderEpisode, ProviderEpisodeCatalog


@dataclass(frozen=True, slots=True)
class NumberingModeInference:
    """One fail-closed group-level numbering-mode decision."""

    attempted: bool
    mode: NumberingMode | None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ObservedMode:
    mode: NumberingMode
    values: tuple[str, ...]


def _has_other_numbering(parse: ParseResult) -> bool:
    return any(
        (
            parse.special_episode is not None,
            parse.episode_date is not None,
            parse.segment_hint is not None,
        )
    )


def _has_complete_aired(parse: ParseResult) -> bool:
    return parse.season is not None and bool(parse.episodes)


def _has_any_aired(parse: ParseResult) -> bool:
    return parse.season is not None or bool(parse.episodes)


def _has_complete_absolute(parse: ParseResult) -> bool:
    return parse.absolute_episode is not None and parse.absolute_episode > 0


def _usable_numbering_parses(
    parses: tuple[ParseResult, ...],
) -> tuple[ParseResult, ...]:
    """Return aired/absolute evidence that can participate in primary-mode choice.

    Specials, dates, and segment-title records are independent accessory families.
    They are assigned separately after the show identity is resolved and must not
    poison the primary aired/absolute decision for unrelated files in the group.
    """

    return tuple(
        parse
        for parse in parses
        if not _has_other_numbering(parse)
        and (_has_any_aired(parse) or parse.absolute_episode is not None)
    )


def _partition_numbering_parses(
    parses: tuple[ParseResult, ...],
) -> tuple[
    tuple[ParseResult, ...],
    tuple[ParseResult, ...],
    tuple[ParseResult, ...],
    tuple[ParseResult, ...],
]:
    dual: list[ParseResult] = []
    aired_only: list[ParseResult] = []
    absolute_only: list[ParseResult] = []
    invalid: list[ParseResult] = []
    for parse in parses:
        has_aired = _has_any_aired(parse)
        aired_complete = _has_complete_aired(parse)
        absolute_complete = _has_complete_absolute(parse)
        has_absolute = parse.absolute_episode is not None

        if aired_complete and absolute_complete:
            dual.append(parse)
        elif aired_complete and not has_absolute:
            aired_only.append(parse)
        elif not has_aired and absolute_complete:
            absolute_only.append(parse)
        else:
            invalid.append(parse)
    return tuple(dual), tuple(aired_only), tuple(absolute_only), tuple(invalid)


def _candidate_observations(
    parses: tuple[ParseResult, ...],
) -> tuple[tuple[_ObservedMode, ...], tuple[str, ...]]:
    relevant = _usable_numbering_parses(parses)
    if not relevant:
        return (), ()

    aired_complete = all(_has_complete_aired(parse) for parse in relevant)
    absolute_complete = all(_has_complete_absolute(parse) for parse in relevant)

    observations: list[_ObservedMode] = []
    if aired_complete:
        aired_values = {
            f"S{parse.season:02d}E{episode:02d}"
            for parse in relevant
            for episode in parse.episodes
            if parse.season is not None
        }
        if aired_values:
            observations.append(
                _ObservedMode(
                    mode=NumberingMode.AIRED,
                    values=tuple(sorted(aired_values)),
                )
            )

    if absolute_complete:
        absolute_values = {
            str(parse.absolute_episode)
            for parse in relevant
            if parse.absolute_episode is not None
        }
        if absolute_values:
            observations.append(
                _ObservedMode(
                    mode=NumberingMode.ABSOLUTE,
                    values=tuple(sorted(absolute_values, key=int)),
                )
            )

    if observations:
        return tuple(observations), ()
    return (), ("numbering-inference:mixed-or-incomplete-group-evidence",)


def _episode_coordinate(episode: ProviderEpisode) -> str:
    if episode.number is None:
        return f"S{episode.season:02d}:unnumbered"
    return f"S{episode.season:02d}E{episode.number:02d}"


def _aired_compatibility(
    catalog: ProviderEpisodeCatalog,
    observed: _ObservedMode,
) -> tuple[bool | None, tuple[str, ...]]:
    by_coordinate: dict[str, list[ProviderEpisode]] = defaultdict(list)
    for episode in catalog.episodes:
        if episode.number is not None:
            by_coordinate[_episode_coordinate(episode)].append(episode)

    ambiguous = tuple(
        coordinate
        for coordinate, episodes in sorted(by_coordinate.items())
        if len(episodes) > 1
    )
    if ambiguous:
        return None, tuple(
            f"numbering-catalog-ambiguous:aired:{coordinate}"
            for coordinate in ambiguous
        )

    missing: list[str] = []
    reasons: list[str] = []
    for value in observed.values:
        matches = by_coordinate.get(value, ())
        if not matches:
            missing.append(value)
            continue
        episode = matches[0]
        reasons.append(f"numbering-map:aired:{value}->{episode.identity.key}")

    compatible = not missing
    return compatible, (
        f"numbering-compatible:{str(compatible).casefold()}:aired",
        *reasons,
        *(f"numbering-missing:aired:{value}" for value in missing),
    )


def _absolute_compatibility(
    catalog: ProviderEpisodeCatalog,
    observed: _ObservedMode,
) -> tuple[bool | None, tuple[str, ...]]:
    regular = tuple(
        sorted(
            (
                episode
                for episode in catalog.episodes
                if episode.season > 0 and episode.number is not None
            ),
            key=lambda episode: (
                episode.season,
                episode.number,
                episode.identity.key,
            ),
        )
    )

    coordinates: dict[tuple[int, int], list[ProviderIdentity]] = defaultdict(list)
    for episode in regular:
        assert episode.number is not None
        coordinates[(episode.season, episode.number)].append(episode.identity)
    ambiguous = tuple(
        coordinate
        for coordinate, identities in sorted(coordinates.items())
        if len(identities) > 1
    )
    if ambiguous:
        return None, tuple(
            f"numbering-catalog-ambiguous:absolute:S{season:02d}E{number:02d}"
            for season, number in ambiguous
        )

    missing: list[str] = []
    reasons: list[str] = []
    for value in observed.values:
        position = int(value)
        if position <= 0 or position > len(regular):
            missing.append(value)
            continue
        episode = regular[position - 1]
        reasons.append(
            "numbering-map:absolute:"
            f"{value}->{_episode_coordinate(episode)}:{episode.identity.key}"
        )

    compatible = not missing
    return compatible, (
        f"numbering-compatible:{str(compatible).casefold()}:absolute",
        *reasons,
        *(f"numbering-missing:absolute:{value}" for value in missing),
    )


def infer_group_numbering_mode(
    parses: tuple[ParseResult, ...],
    catalog: ProviderEpisodeCatalog,
) -> NumberingModeInference:
    """Infer a primary aired/absolute mode without poisoning independent families.

    Dual aired+absolute evidence still requires one unique catalog-compatible mode.
    Separately aired-only and absolute-only files are independent, self-describing
    families; aired remains the deterministic primary while assignment validates
    each family separately. Accessory special/date/segment records are excluded from
    primary-mode inference and cannot make unrelated regular episodes suspicious.
    """

    usable = _usable_numbering_parses(parses)
    if not usable:
        return NumberingModeInference(attempted=False, mode=None)

    dual, aired_only, absolute_only, invalid = _partition_numbering_parses(usable)

    if dual:
        observations, observation_reasons = _candidate_observations(dual)
        context_reasons: tuple[str, ...] = ()
        if len(dual) != len(usable):
            context_reasons = (
                "numbering-inference:dual-evidence-subgroup",
                "numbering-inference:independent-families-deferred-to-assignment",
            )
    elif aired_only and absolute_only:
        primary_reasons = [
            "numbering-inference:mixed-independent-families",
            "numbering-primary:aired",
            "numbering-selected:aired",
        ]
        if invalid:
            primary_reasons.append(
                "numbering-inference:invalid-records-deferred-to-assignment"
            )
        return NumberingModeInference(
            attempted=True,
            mode=NumberingMode.AIRED,
            reasons=tuple(primary_reasons),
        )
    elif aired_only:
        # Aired is the product default and these sources already carry explicit
        # SxxEyy evidence. Any malformed sibling is isolated during assignment.
        if invalid:
            return NumberingModeInference(
                attempted=True,
                mode=NumberingMode.AIRED,
                reasons=(
                    "numbering-inference:primary-aired-with-isolated-invalid-records",
                    "numbering-selected:aired",
                ),
            )
        return NumberingModeInference(attempted=False, mode=None)
    elif absolute_only:
        observations, observation_reasons = _candidate_observations(absolute_only)
        context_reasons = (
            ("numbering-inference:invalid-records-deferred-to-assignment",)
            if invalid
            else ()
        )
    else:
        return NumberingModeInference(
            attempted=True,
            mode=None,
            reasons=("numbering-inference:mixed-or-incomplete-group-evidence",),
        )

    if not observations and not observation_reasons:
        return NumberingModeInference(attempted=False, mode=None)
    if not observations:
        return NumberingModeInference(
            attempted=True,
            mode=None,
            reasons=(*context_reasons, *observation_reasons),
        )

    candidate_reasons = tuple(
        f"numbering-candidate:{observed.mode.value}" for observed in observations
    )
    request_reason = f"numbering-catalog-request:{catalog.request_key}"
    if not catalog.resolved:
        return NumberingModeInference(
            attempted=True,
            mode=None,
            reasons=(
                *context_reasons,
                *candidate_reasons,
                request_reason,
                "numbering-inference:indeterminate-catalog",
                "numbering-catalog-unresolved:"
                f"{catalog.unresolved_reason or 'provider-catalog-unresolved'}",
            ),
        )
    if catalog.errors:
        return NumberingModeInference(
            attempted=True,
            mode=None,
            reasons=(
                *context_reasons,
                *candidate_reasons,
                request_reason,
                "numbering-inference:indeterminate-catalog",
                *(f"numbering-catalog-error:{error}" for error in catalog.errors),
            ),
        )

    compatibility: dict[NumberingMode, bool | None] = {}
    reasons: list[str] = [*context_reasons, *candidate_reasons, request_reason]
    for observed in observations:
        if observed.mode is NumberingMode.AIRED:
            compatible, mode_reasons = _aired_compatibility(catalog, observed)
        else:
            compatible, mode_reasons = _absolute_compatibility(catalog, observed)
        compatibility[observed.mode] = compatible
        reasons.extend(mode_reasons)

    if any(value is None for value in compatibility.values()):
        reasons.append("numbering-inference:indeterminate-catalog")
        return NumberingModeInference(
            attempted=True,
            mode=None,
            reasons=tuple(reasons),
        )

    winners = tuple(
        mode
        for mode in (NumberingMode.AIRED, NumberingMode.ABSOLUTE)
        if compatibility.get(mode) is True
    )
    if len(winners) != 1:
        reasons.append("numbering-inference:no-unique-compatible-mode")
        return NumberingModeInference(
            attempted=True,
            mode=None,
            reasons=tuple(reasons),
        )

    winner = winners[0]
    reasons.extend(
        (
            "numbering-inference:unique-compatible-mode",
            f"numbering-selected:{winner.value}",
        )
    )
    return NumberingModeInference(
        attempted=True,
        mode=winner,
        reasons=tuple(reasons),
    )
