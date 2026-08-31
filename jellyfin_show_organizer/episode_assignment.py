from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Any, cast

from .models import CanonicalShow, MatchEvidence, NumberingMode, ParseResult
from .tvmaze_cache import JsonGetter, TvmazeCatalogCache


class AssignmentStatus(StrEnum):
    MATCHED = "matched"
    SUSPICIOUS = "suspicious"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ProviderEpisode:
    tvmaze_episode_id: int
    season: int
    number: int | None
    title: str
    airdate: str | None = None

    def __post_init__(self) -> None:
        if self.tvmaze_episode_id <= 0:
            raise ValueError("provider episode id must be positive")
        if self.season < 0:
            raise ValueError("provider episode season cannot be negative")
        if self.number is not None and self.number < 0:
            raise ValueError("provider episode number cannot be negative")
        if not self.title:
            raise ValueError("provider episode title cannot be empty")
        if self.airdate is not None:
            try:
                parsed_airdate = date.fromisoformat(self.airdate)
            except ValueError as exc:
                raise ValueError("provider episode airdate must be YYYY-MM-DD") from exc
            if parsed_airdate.isoformat() != self.airdate:
                raise ValueError("provider episode airdate must be canonical YYYY-MM-DD")


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


@dataclass(frozen=True, slots=True)
class _NormalizedCatalog:
    episodes: tuple[ProviderEpisode, ...]
    errors: tuple[str, ...]


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _normalize_catalog(response: object) -> _NormalizedCatalog:
    if not isinstance(response, list):
        return _NormalizedCatalog((), ("episode-catalog-is-not-a-list",))

    episodes: list[ProviderEpisode] = []
    errors: list[str] = []
    for index, item in enumerate(response):
        if not isinstance(item, dict):
            errors.append(f"invalid-catalog-entry:{index}")
            continue
        raw = cast(dict[str, Any], item)
        episode_id = raw.get("id")
        season = raw.get("season")
        number = raw.get("number")
        title = raw.get("name")
        airdate = raw.get("airdate")
        if not isinstance(episode_id, int) or episode_id <= 0:
            errors.append(f"invalid-catalog-episode-id:{index}")
            continue
        if not isinstance(season, int) or season < 0:
            errors.append(f"invalid-catalog-season:{index}")
            continue
        if number is not None and (not isinstance(number, int) or number < 0):
            errors.append(f"invalid-catalog-number:{index}")
            continue
        if not isinstance(title, str) or not title.strip():
            errors.append(f"invalid-catalog-title:{index}")
            continue
        if airdate == "":
            airdate = None
        if airdate is not None:
            if not isinstance(airdate, str):
                errors.append(f"invalid-catalog-airdate:{index}")
                continue
            try:
                parsed_airdate = date.fromisoformat(airdate)
            except ValueError:
                errors.append(f"invalid-catalog-airdate:{index}")
                continue
            if parsed_airdate.isoformat() != airdate:
                errors.append(f"invalid-catalog-airdate:{index}")
                continue
        episodes.append(
            ProviderEpisode(
                tvmaze_episode_id=episode_id,
                season=season,
                number=number,
                title=title.strip(),
                airdate=airdate,
            )
        )

    by_id: dict[int, list[ProviderEpisode]] = defaultdict(list)
    by_coordinate: dict[tuple[int, int], list[ProviderEpisode]] = defaultdict(list)
    for episode in episodes:
        by_id[episode.tvmaze_episode_id].append(episode)
        if episode.number is not None:
            by_coordinate[(episode.season, episode.number)].append(episode)

    for episode_id, matches in sorted(by_id.items()):
        if len(matches) > 1:
            errors.append(f"duplicate-provider-episode-id:{episode_id}")
    for (season, number), matches in sorted(by_coordinate.items()):
        if len(matches) > 1:
            errors.append(f"duplicate-aired-coordinate:S{season:02d}E{number:02d}")

    return _NormalizedCatalog(
        tuple(
            sorted(
                episodes,
                key=lambda episode: (
                    episode.season,
                    episode.number if episode.number is not None else 10**9,
                    episode.tvmaze_episode_id,
                ),
            )
        ),
        tuple(errors),
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


def _evidence_family(parse: ParseResult) -> str:
    has_aired = parse.season is not None or bool(parse.episodes)
    has_absolute = parse.absolute_episode is not None
    has_special = parse.special_kind is not None or parse.special_number is not None
    has_airdate = parse.airdate is not None

    if parse.segment_hint is not None:
        if has_absolute or has_special or has_airdate:
            return "conflict"
        return "segment"

    families = [
        family
        for family, present in (
            ("aired", has_aired),
            ("absolute", has_absolute),
            ("special", has_special),
            ("airdate", has_airdate),
        )
        if present
    ]
    if len(families) > 1:
        return "conflict"
    return families[0] if families else "none"


def _expected_family(mode: NumberingMode) -> str:
    if mode is NumberingMode.AIRED:
        return "aired"
    if mode in {NumberingMode.ABSOLUTE, NumberingMode.PARENTHESIZED_ABSOLUTE}:
        return "absolute"
    if mode is NumberingMode.SEGMENT_TITLE:
        return "segment"
    if mode is NumberingMode.SPECIAL:
        return "special"
    return "airdate"


def _aired_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: _NormalizedCatalog,
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
        parse.absolute_episode is not None
        or parse.segment_hint is not None
        or parse.special_number is not None
        or parse.airdate is not None
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
            f"->tvmaze-episode:{episode.tvmaze_episode_id}"
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
    catalog: _NormalizedCatalog,
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
    if (
        parse.season is not None
        or parse.episodes
        or parse.segment_hint is not None
        or parse.special_number is not None
        or parse.airdate is not None
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
    return _assignment(
        source.source_key,
        AssignmentStatus.MATCHED,
        "episode-catalog",
        f"numbering-mode:{show.numbering_mode.value}",
        f"catalog-request:{request_key}",
        f"absolute-match:{absolute}->S{episode.season:02d}E{episode.number:02d}",
        f"tvmaze-episode:{episode.tvmaze_episode_id}",
        episodes=(episode,),
        confidence=1.0,
    )


def _special_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: _NormalizedCatalog,
    request_key: str,
) -> SourceEpisodeAssignment:
    parse = source.parse
    if parse.special_kind is None or parse.special_number is None:
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
        or parse.airdate is not None
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
        episode
        for episode in catalog.episodes
        if episode.season == 0 and episode.number == parse.special_number
    )
    special_token = f"{parse.special_kind}:{parse.special_number}"
    if not matches:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"special-kind:{parse.special_kind}",
            f"missing-special-catalog-entry:{special_token}",
            f"catalog-request:{request_key}",
        )
    if len(matches) > 1:
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"special-kind:{parse.special_kind}",
            f"ambiguous-special-catalog-entry:{special_token}",
            f"catalog-request:{request_key}",
        )

    episode = matches[0]
    return _assignment(
        source.source_key,
        AssignmentStatus.MATCHED,
        "episode-catalog",
        f"numbering-mode:{show.numbering_mode.value}",
        f"special-kind:{parse.special_kind}",
        f"special-match:{special_token}->S00E{parse.special_number:02d}",
        f"catalog-request:{request_key}",
        f"tvmaze-episode:{episode.tvmaze_episode_id}",
        episodes=(episode,),
        confidence=1.0,
    )


def _airdate_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: _NormalizedCatalog,
    request_key: str,
) -> SourceEpisodeAssignment:
    parse = source.parse
    if parse.airdate is None:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            "missing-airdate-numbering-evidence",
            f"catalog-request:{request_key}",
        )
    if (
        parse.season is not None
        or parse.episodes
        or parse.absolute_episode is not None
        or parse.segment_hint is not None
        or parse.special_number is not None
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
        episode for episode in catalog.episodes if episode.airdate == parse.airdate
    )
    if not matches:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"missing-airdate-catalog-entry:{parse.airdate}",
            f"catalog-request:{request_key}",
        )
    if len(matches) > 1:
        return _assignment(
            source.source_key,
            AssignmentStatus.SUSPICIOUS,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"ambiguous-airdate-catalog-entry:{parse.airdate}",
            f"catalog-request:{request_key}",
        )

    episode = matches[0]
    if episode.number is None:
        return _assignment(
            source.source_key,
            AssignmentStatus.UNRESOLVED,
            "episode-catalog",
            f"numbering-mode:{show.numbering_mode.value}",
            f"airdate-match-provider-number-missing:{parse.airdate}",
            f"catalog-request:{request_key}",
        )
    return _assignment(
        source.source_key,
        AssignmentStatus.MATCHED,
        "episode-catalog",
        f"numbering-mode:{show.numbering_mode.value}",
        f"airdate-match:{parse.airdate}->S{episode.season:02d}E{episode.number:02d}",
        f"catalog-request:{request_key}",
        f"tvmaze-episode:{episode.tvmaze_episode_id}",
        episodes=(episode,),
        confidence=1.0,
    )


def _segment_assignment(
    source: SourceEpisodeInput,
    show: CanonicalShow,
    catalog: _NormalizedCatalog,
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
        f"tvmaze-episode:{episode.tvmaze_episode_id}",
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
    by_episode: dict[int, list[SourceEpisodeAssignment]] = defaultdict(list)
    for assignment in assignments:
        if assignment.status is not AssignmentStatus.MATCHED:
            continue
        for episode in assignment.episodes:
            by_episode[episode.tvmaze_episode_id].append(assignment)

    reasons_by_source: dict[str, list[str]] = defaultdict(list)
    for episode_id, matches in sorted(by_episode.items()):
        source_keys = sorted(
            {match.source_key for match in matches},
            key=lambda source_key: (source_key.casefold(), source_key),
        )
        if len(source_keys) <= 1:
            continue
        reason = f"duplicate-provider-episode-assignment:tvmaze-episode:{episode_id}"
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
    by_episode: dict[int, list[SourceEpisodeAssignment]] = defaultdict(list)
    for assignment in assignments:
        if assignment.status is AssignmentStatus.MATCHED:
            for episode in assignment.episodes:
                by_episode[episode.tvmaze_episode_id].append(assignment)

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


def assign_episode_group(
    show: CanonicalShow,
    sources: Iterable[SourceEpisodeInput],
    cache: TvmazeCatalogCache,
    getter: JsonGetter,
) -> EpisodeGroupAssignment:
    """Assign episode identities for one canonical source-show group.

    The provider episode catalog is requested once for the whole group. The
    function never searches for a show and never performs per-file provider
    lookups. Callers must remove extra videos before invoking this layer.
    """

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

    explicit_ids = {
        source.parse.embedded_tvmaze_id
        for source in source_group
        if source.parse.embedded_tvmaze_id is not None
    }
    if any(tvmaze_id != show.tvmaze_id for tvmaze_id in explicit_ids):
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
        _evidence_family(source.parse)
        for source in source_group
        if _evidence_family(source.parse) != "none"
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

    cache_record = cache.episode_catalog(show.tvmaze_id, getter)
    request_key = cache_record.request_key
    if not cache_record.resolved:
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.UNRESOLVED,
                "episode-catalog",
                f"numbering-mode:{show.numbering_mode.value}",
                f"catalog-request:{request_key}",
                f"catalog-unresolved:{cache_record.unresolved_reason or 'unknown'}",
            )
            for source in source_group
        )
        return EpisodeGroupAssignment(
            show, AssignmentStatus.UNRESOLVED, assignments, request_key
        )

    catalog = _normalize_catalog(cache_record.response)
    if catalog.errors:
        assignments = tuple(
            _assignment(
                source.source_key,
                AssignmentStatus.UNRESOLVED,
                "episode-catalog",
                f"numbering-mode:{show.numbering_mode.value}",
                f"catalog-request:{request_key}",
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
    elif show.numbering_mode is NumberingMode.SEGMENT_TITLE:
        matcher = _segment_assignment
    elif show.numbering_mode is NumberingMode.SPECIAL:
        matcher = _special_assignment
    elif show.numbering_mode is NumberingMode.AIRDATE:
        matcher = _airdate_assignment

    assignments = tuple(
        matcher(source, show, catalog, request_key) for source in source_group
    )
    if show.numbering_mode is NumberingMode.SEGMENT_TITLE:
        assignments = _protect_segment_identity(source_group, assignments)
    else:
        assignments = _protect_provider_episode_identity(assignments)

    return EpisodeGroupAssignment(
        show=show,
        status=_group_status(assignments),
        assignments=assignments,
        catalog_request_key=request_key,
    )
