from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import ParseResult
from .providers import ProviderEpisode, ProviderEpisodeCatalog

_MIN_EXACT_MATCHES = 3
_MIN_COORDINATE_DISAGREEMENTS = 2
_NEAR_TITLE_THRESHOLD = 0.92
_NEAR_TITLE_GAP = 0.08
_MIN_NEAR_TITLE_LENGTH = 8
_TECHNICAL_SUFFIX = re.compile(
    r"(?i)(?:[ ._-]+(?:"
    r"repack|proper|v\d+|ntsc|pal|dvd|bd|"
    r"(?:aac|ddp|eac3|ac3|flac|opus)(?:[ ._-]?\d(?:[ ._-]\d)?)?"
    r"))+$"
)


@dataclass(frozen=True, slots=True)
class SegmentCountedTitleObservation:
    parse_index: int
    normalized_title: str
    episode: ProviderEpisode | None
    ambiguous: bool
    coordinate_disagrees: bool


@dataclass(frozen=True, slots=True)
class SegmentCountedTitleRecovery:
    parse_index: int
    episode: ProviderEpisode
    score: float


@dataclass(frozen=True, slots=True)
class SegmentCountedTitleAnalysis:
    observations: tuple[SegmentCountedTitleObservation, ...]
    eligible_count: int
    exact_match_count: int
    ambiguous_count: int
    coordinate_disagreement_count: int
    one_to_one: bool
    triggered: bool
    proven: bool
    reasons: tuple[str, ...]


def normalize_episode_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    normalized = re.sub(r"(?i)([A-Za-z0-9])['’]s\b", r"\1s", normalized)
    normalized = "".join(
        character for character in normalized if unicodedata.category(character) != "Mn"
    ).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def clean_episode_title_hint(value: str) -> str:
    cleaned = unicodedata.normalize("NFKC", value).strip(" ._-")
    while True:
        trimmed = _TECHNICAL_SUFFIX.sub("", cleaned).rstrip(" ._-")
        if trimmed == cleaned:
            break
        cleaned = trimmed
    return normalize_episode_title(cleaned)


def _optional_leading_the_key(normalized_title: str) -> str:
    tokens = normalized_title.split()
    if len(tokens) > 1 and tokens[0] == "the":
        return " ".join(tokens[1:])
    return normalized_title


def is_segment_counted_title_candidate(parse: ParseResult) -> bool:
    return (
        parse.season is not None
        and parse.season > 0
        and bool(parse.episodes)
        and parse.absolute_episode is None
        and parse.special_episode is None
        and parse.special_kind is None
        and parse.episode_date is None
        and parse.segment_hint is None
        and parse.title_hint is not None
        and bool(clean_episode_title_hint(parse.title_hint))
    )


def analyze_segment_counted_titles(
    parses: tuple[ParseResult, ...],
    catalog: ProviderEpisodeCatalog,
) -> SegmentCountedTitleAnalysis:
    """Prove a segment-counted aired family only from repeated exact titles."""

    by_season_title: dict[tuple[int, str], list[ProviderEpisode]] = {}
    for catalog_episode in catalog.episodes:
        if catalog_episode.season <= 0 or catalog_episode.number is None:
            continue
        normalized = normalize_episode_title(catalog_episode.title)
        if not normalized:
            continue
        by_season_title.setdefault((catalog_episode.season, normalized), []).append(
            catalog_episode
        )

    observations: list[SegmentCountedTitleObservation] = []
    for index, parse in enumerate(parses):
        if not is_segment_counted_title_candidate(parse):
            continue
        assert parse.season is not None
        assert parse.title_hint is not None
        normalized_title = clean_episode_title_hint(parse.title_hint)
        matches = tuple(by_season_title.get((parse.season, normalized_title), ()))
        selected_episode = matches[0] if len(matches) == 1 else None
        observations.append(
            SegmentCountedTitleObservation(
                parse_index=index,
                normalized_title=normalized_title,
                episode=selected_episode,
                ambiguous=len(matches) > 1,
                coordinate_disagrees=(
                    selected_episode is not None
                    and selected_episode.number is not None
                    and parse.episodes != (selected_episode.number,)
                ),
            )
        )

    exact = tuple(
        observation for observation in observations if observation.episode is not None
    )
    identities = tuple(
        observation.episode.identity for observation in exact if observation.episode
    )
    one_to_one = len(identities) == len(set(identities))
    eligible_count = len(observations)
    exact_match_count = len(exact)
    ambiguous_count = sum(observation.ambiguous for observation in observations)
    disagreement_count = sum(
        observation.coordinate_disagrees for observation in observations
    )
    triggered = (
        exact_match_count >= _MIN_EXACT_MATCHES
        and disagreement_count >= _MIN_COORDINATE_DISAGREEMENTS
    )
    proven = (
        triggered
        and eligible_count >= _MIN_EXACT_MATCHES
        and exact_match_count * 2 >= eligible_count
        and one_to_one
    )
    return SegmentCountedTitleAnalysis(
        observations=tuple(observations),
        eligible_count=eligible_count,
        exact_match_count=exact_match_count,
        ambiguous_count=ambiguous_count,
        coordinate_disagreement_count=disagreement_count,
        one_to_one=one_to_one,
        triggered=triggered,
        proven=proven,
        reasons=(
            f"segment-counted-title-eligible:{eligible_count}",
            f"segment-counted-title-exact-matches:{exact_match_count}/{eligible_count}",
            f"segment-counted-title-ambiguous:{ambiguous_count}",
            f"segment-counted-title-coordinate-disagreements:{disagreement_count}",
            f"segment-counted-title-one-to-one:{str(one_to_one).casefold()}",
            f"segment-counted-title-triggered:{str(triggered).casefold()}",
            f"segment-counted-title-compatible:{str(proven).casefold()}",
        ),
    )


def recover_unique_near_segment_titles(
    parses: tuple[ParseResult, ...],
    catalog: ProviderEpisodeCatalog,
    analysis: SegmentCountedTitleAnalysis,
) -> tuple[SegmentCountedTitleRecovery, ...]:
    """Recover one near-title member only after exact evidence proves the group."""

    if not analysis.proven:
        return ()

    claimed = {
        observation.episode.identity
        for observation in analysis.observations
        if observation.episode is not None
    }
    tentative: list[SegmentCountedTitleRecovery] = []
    for observation in analysis.observations:
        if observation.episode is not None or observation.ambiguous:
            continue
        if len(observation.normalized_title) < _MIN_NEAR_TITLE_LENGTH:
            continue
        parse = parses[observation.parse_index]
        if parse.season is None:
            continue

        source_article_key = _optional_leading_the_key(observation.normalized_title)
        scored: list[tuple[float, ProviderEpisode]] = []
        for episode in catalog.episodes:
            if episode.season != parse.season or episode.number is None:
                continue
            candidate_title = normalize_episode_title(episode.title)
            if len(candidate_title) < _MIN_NEAR_TITLE_LENGTH:
                continue
            if (
                candidate_title != observation.normalized_title
                and _optional_leading_the_key(candidate_title) == source_article_key
            ):
                score = 1.0
            else:
                score = SequenceMatcher(
                    None, observation.normalized_title, candidate_title, autojunk=False
                ).ratio()
            scored.append((score, episode))
        scored.sort(key=lambda item: (-item[0], item[1].identity.key))
        if not scored or scored[0][0] < _NEAR_TITLE_THRESHOLD:
            continue
        top_score, top_episode = scored[0]
        runner_score = scored[1][0] if len(scored) > 1 else 0.0
        if top_score - runner_score < _NEAR_TITLE_GAP:
            continue
        if top_episode.identity in claimed:
            continue
        tentative.append(
            SegmentCountedTitleRecovery(
                parse_index=observation.parse_index,
                episode=top_episode,
                score=top_score,
            )
        )

    identity_counts: dict[object, int] = {}
    for recovery in tentative:
        identity_counts[recovery.episode.identity] = (
            identity_counts.get(recovery.episode.identity, 0) + 1
        )
    return tuple(
        recovery
        for recovery in tentative
        if identity_counts[recovery.episode.identity] == 1
    )