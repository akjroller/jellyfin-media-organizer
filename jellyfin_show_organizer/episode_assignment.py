from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from .models import (
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    ParseResult,
    ProviderIdentity,
)
from .providers import (
    MetadataProvider,
    ProviderEpisode,
    ProviderEpisodeCatalog,
    TvmazeProviderAdapter,
)
from .tvmaze_cache import JsonGetter, TvmazeCatalogCache


class AssignmentStatus(StrEnum):
    MATCHED = "matched"
    SUSPICIOUS = "suspicious"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class SourceEpisodeInput:
    source_key: str
    parse: ParseResult

    def __post_init__(self) -> None:
        if not self.source_key:
            raise ValueError("source episode input requires a source_key")


@dataclass(frozen=True, slots=True)
class SourceEpisodeAssignment:
    source_key: str
    status: AssignmentStatus
    episodes: tuple[ProviderEpisode, ...]
    evidence: MatchEvidence

    def __post_init__(self) -> None:
        if not self.source_key:
            raise ValueError("source episode assignment requires a source_key")
        if self.status is AssignmentStatus.MATCHED and not self.episodes:
            raise ValueError("matched episode assignments require provider episodes")
        if self.status is not AssignmentStatus.MATCHED and self.episodes:
            raise ValueError("non-matched episode assignments cannot carry episodes")


@dataclass(frozen=True, slots=True)
class EpisodeGroupAssignment:
    show: CanonicalShow
    status: AssignmentStatus
    assignments: tuple[SourceEpisodeAssignment, ...]
    catalog_request_key: str | None


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _episode_identity_reason(episode: ProviderEpisode) -> str:
    if episode.provider == "tvmaze":
        return f"tvmaze-episode:{episode.identity.require_positive_int('tvmaze')}"
    return f"provider-episode:{episode.identity.key}"


def _duplicate_identity_reason(identity: ProviderIdentity) -> str:
    if identity.provider == "tvmaze":
        return f"tvmaze-episode:{identity.require_positive_int('tvmaze')}"
    return f"provider-episode:{identity.key}"


def _catalog_diagnostic_reasons(catalog: ProviderEpisodeCatalog) -> tuple[str, ...]:
    return tuple(
        f"catalog-diagnostic:{diagnostic}" for diagnostic in catalog.diagnostics
    )


def _append_catalog_diagnostics(
    assignments: tuple[SourceEpisodeAssignment, ...],
    catalog: ProviderEpisodeCatalog,
) -> tuple[SourceEpisodeAssignment, ...]:
    diagnostic_reasons = _catalog_diagnostic_reasons(catalog)
    if not diagnostic_reasons:
        return assignments
    return tuple(
        replace(
            assignment,
            evidence=replace(
                assignment.evidence,
                reasons=(*assignment.evidence.reasons, *diagnostic_reasons),
            ),
        )
        for assignment in assignments
    )


def _assignment(
    source_key: str,
    status: AssignmentStatus,
    method: str,
    *reasons: str,
    episodes: tuple[ProviderEpisode, ...] = (),
    confidence: float = 0.0,
) -> SourceEpisodeAssignment:
    return SourceEpisodeAssignment(
        source_key=source_key,
        status=status,
        episodes=episodes,
        evidence=MatchEvidence(
            method=method,
            confidence=confidence,
            reasons=tuple(reasons),
        ),
    )


def _expected_family(mode: NumberingMode) -> str:
    if mode is NumberingMode.AIRED:
        return "aired"
    if mode in {NumberingMode.ABSOLUTE, NumberingMode.PARENTHESIZED_ABSOLUTE}:
        return "absolute"
    if mode is NumberingMode.SPECIAL:
        return "special"
    if mode is NumberingMode.DATE:
        return "date"
    return "segment"


def _evidence_family(parse: ParseResult, mode: NumberingMode) -> str:
    has_aired = parse.season is not None or bool(parse.episodes)
    has_complete_aired = parse.season is not None and bool(parse.episodes)
    has_absolute = parse.absolute_episode is not None
    has_special = parse.special_kind is not None or parse.special_episode is not None
    has_date = parse.episode_date is not None

    if parse.segment_hint is not None:
        if has_absolute or has_special or has_date:
            return "conflict"
        return "segment"

    if has_aired and has_absolute:
        if not has_complete_aired or has_special or has_date:
            return "conflict"
        selected = _expected_family(mode)
        if selected in {"aired", "absolute"}:
            return selected
        return "conflict"

    families = [
        family
        for family, present in (
            ("aired", has_aired),
            ("absolute", has_absolute),
            ("special", has_special),
            ("date", has_date),
        )
        if present
    ]
    if len(families) > 1:
        return "conflict"
    return families[0] if families else "none"


def _dual_aired_reason(parse: ParseResult) -> str | None:
    if parse.absolute_episode is None:
        return None
    return f"dual-numbering-evidence:secondary-absolute:{parse.absolute_episode}"


def _dual_absolute_reason(parse: ParseResult) -> str | None:
    if parse.season is None or not parse.episodes:
        return None
    coordinates = ",".join(
        f"S{parse.season:02d}E{episode:02d}" for episode in parse.episodes
    )
    return f"dual-numbering-evidence:secondary-aired:{coordinates}"


def _aired_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: ProviderEpisodeCatalog,
    request_key: str,
) -> SourceEpisodeAssignment:
    parse = source.parse
    if parse.season is None or not parse.episodes:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "missing-aired-numbering-evidence",
            f"catalog-request:{request_key}",
        )
    if (
        parse.segment_hint is not None
        or parse.special_kind is not None
        or parse.episode_date is not None
    ):
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "conflicting-numbering-evidence",
            f"catalog-request:{request_key}",
        )
    if len(set(parse.episodes)) != len(parse.episodes):
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            "duplicate-source-episode-number",
            f"catalog-request:{request_key}",
        )

    by_coordinate = {
        (episode.season, episode.number): episode
        for episode in catalog.episodes
        if episode.number is not None
    }
    matches: list[ProviderEpisode] = []
    reasons = [
        f"numbering-mode:{show.numbering_mode.value}",
        f"catalog-request:{request_key}",
    ]
    dual_reason = _dual_aired_reason(parse)
    if dual_reason is not None:
        reasons.append(dual_reason)
    for number in parse.episodes:
        episode = by_coordinate.get((parse.season, number))
        if episode is None:
            return _assignment(
                source.source_key,
                AssignmentStatus.UNRESOLVED,
                "episode-catalog",
                *reasons,
                f"missing-aired-catalog-entry:S{parse.season:02d}E{number:02d}",
            )
        matches.append(episode)
        reasons.append(
            f"catalog-match:S{parse.season:02d}E{number:02d}"
            f"->{_episode_identity_reason(episode)}"
        )

    return _assignment(
        source.source_key,
        AssignmentStatus.MATCHED,
        "episode-catalog",
        *reasons,
        episodes=tuple(matches),
        confidence=1.0,
    )


def _absolute_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: ProviderEpisodeCatalog,
    request_key: str,
) -> SourceEpisodeAssignment:
    parse = source.parse
    if parse.absolute_episode is None:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "missing-absolute-numbering-evidence",
            f"catalog-request:{request_key}",
        )
    has_aired = parse.season is not None or bool(parse.episodes)
    if (
        (has_aired and (parse.season is None or not parse.episodes))
        or parse.segment_hint is not None
        or parse.special_kind is not None
        or parse.episode_date is not None
    ):
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "conflicting-numbering-evidence",
            f"catalog-request:{request_key}",
        )

    regular = tuple(
        episode
        for episode in catalog.episodes
        if episode.season > 0 and episode.number is not None
    )
    absolute = parse.absolute_episode
    if absolute <= 0 or absolute > len(regular):
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"missing-absolute-catalog-entry:{absolute}",
            f"catalog-request:{request_key}",
        )

    episode = regular[absolute - 1]
    reasons = [
        f"numbering-mode:{show.numbering_mode.value}",
        f"catalog-request:{request_key}",
    ]
    dual_reason = _dual_absolute_reason(parse)
    if dual_reason is not None:
        reasons.append(dual_reason)
    reasons.extend(
        (
            f"absolute-match:{absolute}->S{episode.season:02d}E{episode.number:02d}",
            _episode_identity_reason(episode),
        )
    )
    return _assignment(
        source.source_key,
        AssignmentStatus.MATCHED,
        "episode-catalog",
        *reasons,
        episodes=(episode,),
        confidence=1.0,
    )


def _special_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: ProviderEpisodeCatalog,
    request_key: str,
) -> SourceEpisodeAssignment:
    parse = source.parse
    if parse.special_kind is None or parse.special_episode is None:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "missing-special-numbering-evidence",
            f"catalog-request:{request_key}",
        )
    if (
        parse.season is not None
        or parse.episodes
        or parse.absolute_episode is not None
        or parse.segment_hint is not None
        or parse.episode_date is not None
    ):
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "conflicting-numbering-evidence",
            f"catalog-request:{request_key}",
        )

    candidates = tuple(
        episode
        for episode in catalog.episodes
        if episode.number == parse.special_episode
        and (
            episode.season == 0
            or (episode.episode_type is not None and episode.episode_type != "regular")
        )
    )
    if not candidates:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"special-kind:{parse.special_kind}",
            f"missing-special-catalog-entry:{parse.special_episode}",
            f"catalog-request:{request_key}",
        )

    selected = candidates
    if len(candidates) > 1:
        kind_token = f" {parse.special_kind} "
        kind_matches = tuple(
            episode
            for episode in candidates
            if kind_token in f" {_normalize_title(episode.title)} "
        )
        if len(kind_matches) == 1:
            selected = kind_matches
        else:
            return _assignment(
                source.source_key,
                AssignmentStatus.SUSPICIOUS,
                "episode-catalog",
                f"numbering-mode:{show.numbering_mode.value}",
                f"special-kind:{parse.special_kind}",
                f"ambiguous-special-catalog-entry:{parse.special_episode}",
                f"catalog-request:{request_key}",
            )

    episode = selected[0]
    return _assignment(
        source.source_key,
        AssignmentStatus.MATCHED,
        "episode-catalog",
        f"numbering-mode:{show.numbering_mode.value}",
        f"special-kind:{parse.special_kind}",
        f"special-number:{parse.special_episode}",
        f"special-match:{parse.special_kind.upper()}{parse.special_episode}"
        f"->S{episode.season:02d}E{episode.number:02d}",
        _episode_identity_reason(episode),
        f"catalog-request:{request_key}",
        episodes=(episode,),
        confidence=1.0,
    )


def _date_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: ProviderEpisodeCatalog,
    request_key: str,
) -> SourceEpisodeAssignment:
    parse = source.parse
    if parse.episode_date is None:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "missing-date-numbering-evidence",
            f"catalog-request:{request_key}",
        )
    if (
        parse.season is not None
        or parse.episodes
        or parse.absolute_episode is not None
        or parse.segment_hint is not None
        or parse.special_kind is not None
    ):
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "conflicting-numbering-evidence",
            f"catalog-request:{request_key}",
        )

    matches = tuple(
        episode for episode in catalog.episodes if episode.airdate == parse.episode_date
    )
    if not matches:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"missing-date-catalog-entry:{parse.episode_date}",
            f"catalog-request:{request_key}",
        )
    if any(episode.number is None for episode in matches):
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"date-catalog-entry-missing-number:{parse.episode_date}",
            f"catalog-request:{request_key}",
        )
    if len(matches) > 1:
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"ambiguous-date-catalog-entry:{parse.episode_date}",
            f"catalog-request:{request_key}",
        )

    episode = matches[0]
    return _assignment(
        source.source_key,
        AssignmentStatus.MATCHED,
        "episode-catalog",
        f"numbering-mode:{show.numbering_mode.value}",
        f"date-match:{parse.episode_date}->S{episode.season:02d}E{episode.number:02d}",
        _episode_identity_reason(episode),
        f"catalog-request:{request_key}",
        episodes=(episode,),
        confidence=1.0,
    )


def _segment_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: ProviderEpisodeCatalog,
    request_key: str,
) -> SourceEpisodeAssignment:
    parse = source.parse
    if parse.segment_hint is None:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "missing-segment-hint",
            f"catalog-request:{request_key}",
        )
    if parse.title_hint is None or not parse.title_hint.strip():
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"segment-hint:{parse.segment_hint.casefold()}",
            "missing-segment-title-evidence",
            f"catalog-request:{request_key}",
        )

    normalized_title = _normalize_title(parse.title_hint)
    matches = tuple(
        episode
        for episode in catalog.episodes
        if _normalize_title(episode.title) == normalized_title
    )
    if not matches:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"segment-hint:{parse.segment_hint.casefold()}",
            f"missing-segment-title-match:{normalized_title}",
            f"catalog-request:{request_key}",
        )
    if len(matches) > 1:
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"segment-hint:{parse.segment_hint.casefold()}",
            f"ambiguous-segment-title-match:{normalized_title}",
            f"catalog-request:{request_key}",
        )

    episode = matches[0]
    return _assignment(
        source.source_key,
        AssignmentStatus.MATCHED,
        "episode-catalog",
        f"numbering-mode:{show.numbering_mode.value}",
        f"segment-hint:{parse.segment_hint.casefold()}",
        f"segment-title-match:{normalized_title}",
        f"catalog-request:{request_key}",
        _episode_identity_reason(episode),
        episodes=(episode,),
        confidence=1.0,
    )


def _group_status(assignments: Iterable[SourceEpisodeAssignment]) -> AssignmentStatus:
    statuses = {assignment.status for assignment in assignments}
    if statuses == {AssignmentStatus.MATCHED}:
        return AssignmentStatus.MATCHED
    if AssignmentStatus.SUSPICIOUS in statuses:
        return AssignmentStatus.SUSPICIOUS
    return AssignmentStatus.UNRESOLVED


def _protect_provider_episode_identity(
    assignments: tuple[SourceEpisodeAssignment, ...],
) -> tuple[SourceEpisodeAssignment, ...]:
    by_episode: dict[ProviderIdentity, list[SourceEpisodeAssignment]] = defaultdict(
        list
    )
    for assignment in assignments:
        if assignment.status is not AssignmentStatus.MATCHED:
            continue
        for episode in assignment.episodes:
            by_episode[episode.identity].append(assignment)

    reasons_by_source: dict[str, list[str]] = defaultdict(list)
    for episode_id, matches in sorted(by_episode.items(), key=lambda item: item[0].key):
        source_keys = sorted(
            {match.source_key for match in matches},
            key=lambda source_key: (source_key.casefold(), source_key),
        )
        if len(source_keys) <= 1:
            continue
        reason = (
            "duplicate-provider-episode-assignment:"
            f"{_duplicate_identity_reason(episode_id)}"
        )
        for source_key in source_keys:
            reasons_by_source[source_key].append(reason)

    if not reasons_by_source:
        return assignments

    protected: list[SourceEpisodeAssignment] = []
    for assignment in assignments:
        duplicate_reasons = reasons_by_source.get(assignment.source_key)
        if not duplicate_reasons:
            protected.append(assignment)
            continue
        protected.append(
            replace(
                assignment,
                status=AssignmentStatus.SUSPICIOUS,
                episodes=(),
                evidence=replace(
                    assignment.evidence,
                    confidence=0.0,
                    reasons=(
                        *assignment.evidence.reasons,
                        *duplicate_reasons,
                    ),
                ),
            )
        )
    return tuple(protected)


def _protect_segment_identity(
    sources: tuple[SourceEpisodeInput, ...],
    assignments: tuple[SourceEpisodeAssignment, ...],
) -> tuple[SourceEpisodeAssignment, ...]:
    parse_by_key = {source.source_key: source.parse for source in sources}
    by_episode: dict[ProviderIdentity, list[SourceEpisodeAssignment]] = defaultdict(
        list
    )
    for assignment in assignments:
        if assignment.status is AssignmentStatus.MATCHED:
            for episode in assignment.episodes:
                by_episode[episode.identity].append(assignment)

    collapsed_keys: set[str] = set()
    for matches in by_episode.values():
        segment_hints: set[str] = set()
        for match in matches:
            segment_hint = parse_by_key[match.source_key].segment_hint
            if segment_hint is not None:
                segment_hints.add(segment_hint.casefold())
        if len(segment_hints) > 1:
            collapsed_keys.update(match.source_key for match in matches)

    if not collapsed_keys:
        return assignments

    protected: list[SourceEpisodeAssignment] = []
    for assignment in assignments:
        if assignment.source_key not in collapsed_keys:
            protected.append(assignment)
            continue
        protected.append(
            replace(
                assignment,
                status=AssignmentStatus.SUSPICIOUS,
                episodes=(),
                evidence=replace(
                    assignment.evidence,
                    confidence=0.0,
                    reasons=(
                        *assignment.evidence.reasons,
                        "distinct-segments-collapse-to-same-catalog-episode",
                    ),
                ),
            )
        )
    return tuple(protected)


def assign_episode_group_with_provider(
    show: CanonicalShow,
    sources: Iterable[SourceEpisodeInput],
    provider: MetadataProvider,
) -> EpisodeGroupAssignment:
    """Assign episodes from one normalized metadata-provider catalog."""

    source_group = tuple(
        sorted(
            sources,
            key=lambda source: (source.source_key.casefold(), source.source_key),
        )
    )
    if not source_group:
        raise ValueError("episode assignment requires at least one source")
    source_keys = [source.source_key for source in source_group]
    if len(set(source_keys)) != len(source_keys):
        raise ValueError("episode assignment source_key values must be unique")

    if show.provider != provider.provider_name:
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.SUSPICIOUS,
                "group-validation",
                "canonical-show-provider-does-not-match-active-provider",
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.SUSPICIOUS, assignments, None
        )

    explicit_identities = {
        identity
        for source in source_group
        for identity in source.parse.provider_identities
    }
    if any(identity != show.provider_identity for identity in explicit_identities):
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.SUSPICIOUS,
                "group-validation",
                "source-provider-id-conflicts-with-canonical-show",
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.SUSPICIOUS, assignments, None
        )

    families = {
        family
        for source in source_group
        if (
            family := _evidence_family(source.parse, show.numbering_mode)
        )
        != "none"
    }
    if "conflict" in families or len(families) > 1:
        reason = "mixed-numbering-evidence:" + ",".join(sorted(families))
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.SUSPICIOUS,
                "group-validation",
                reason,
                f"configured-numbering-mode:{show.numbering_mode.value}",
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.SUSPICIOUS, assignments, None
        )

    expected_family = _expected_family(show.numbering_mode)
    if families and families != {expected_family}:
        reason = (
            f"numbering-policy-conflict:expected-{expected_family}:"
            f"observed-{next(iter(families))}"
        )
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.SUSPICIOUS,
                "group-validation",
                reason,
                f"configured-numbering-mode:{show.numbering_mode.value}",
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.SUSPICIOUS, assignments, None
        )

    catalog = provider.episode_catalog(show.provider_identity)
    request_key = catalog.request_key
    if catalog.show_identity != show.provider_identity:
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.SUSPICIOUS,
                "episode-catalog",
                "provider-catalog-show-identity-mismatch",
                f"requested-show:{show.provider_identity.key}",
                f"catalog-show:{catalog.show_identity.key}",
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.SUSPICIOUS, assignments, request_key
        )
    if not catalog.resolved:
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.UNRESOLVED,
                "episode-catalog",
                f"numbering-mode:{show.numbering_mode.value}",
                f"catalog-request:{request_key}",
                f"catalog-unresolved:{catalog.unresolved_reason or 'unknown'}",
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.UNRESOLVED, assignments, request_key
        )

    diagnostic_reasons = _catalog_diagnostic_reasons(catalog)
    if catalog.errors:
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.UNRESOLVED,
                "episode-catalog",
                f"numbering-mode:{show.numbering_mode.value}",
                f"catalog-request:{request_key}",
                *diagnostic_reasons,
                *catalog.errors,
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.UNRESOLVED, assignments, request_key
        )
    if not catalog.episodes:
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.UNRESOLVED,
                "episode-catalog",
                f"numbering-mode:{show.numbering_mode.value}",
                f"catalog-request:{request_key}",
                *diagnostic_reasons,
                "empty-episode-catalog",
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.UNRESOLVED, assignments, request_key
        )

    matcher = _aired_assignment
    if show.numbering_mode in {
        NumberingMode.ABSOLUTE,
        NumberingMode.PARENTHESIZED_ABSOLUTE,
    }:
        matcher = _absolute_assignment
    elif show.numbering_mode is NumberingMode.SPECIAL:
        matcher = _special_assignment
    elif show.numbering_mode is NumberingMode.DATE:
        matcher = _date_assignment
    elif show.numbering_mode is NumberingMode.SEGMENT_TITLE:
        matcher = _segment_assignment

    assignments = tuple(
        matcher(source, show, catalog, request_key) for source in source_group
    )
    if show.numbering_mode is NumberingMode.SEGMENT_TITLE:
        assignments = _protect_segment_identity(source_group, assignments)
    else:
        assignments = _protect_provider_episode_identity(assignments)
    assignments = _append_catalog_diagnostics(assignments, catalog)

    return EpisodeGroupAssignment(
        show=show,
        status=_group_status(assignments),
        assignments=assignments,
        catalog_request_key=request_key,
    )


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
