from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import ParseResult
from .providers import ProviderEpisode, ProviderEpisodeCatalog

_MIN_EXACT_MATCHES = 3
_MIN_COORDINATE_DISAGREEMENTS = 2
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
class SegmentCountedTitleAnalysis:
    observations: tuple[SegmentCountedTitleObservation, ...]
    eligible_count: int
    exact_match_count: int
    ambiguous_count: int
    coordinate_disagreement_count: int
    one_to_one: bool
    proven: bool
    reasons: tuple[str, ...]


def normalize_episode_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
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
    for episode in catalog.episodes:
        if episode.season <= 0 or episode.number is None:
            continue
        normalized = normalize_episode_title(episode.title)
        if not normalized:
            continue
        by_season_title.setdefault((episode.season, normalized), []).append(episode)

    observations: list[SegmentCountedTitleObservation] = []
    for index, parse in enumerate(parses):
        if not is_segment_counted_title_candidate(parse):
            continue
        assert parse.season is not None
        assert parse.title_hint is not None
        normalized_title = clean_episode_title_hint(parse.title_hint)
        matches = tuple(by_season_title.get((parse.season, normalized_title), ()))
        episode = matches[0] if len(matches) == 1 else None
        observations.append(
            SegmentCountedTitleObservation(
                parse_index=index,
                normalized_title=normalized_title,
                episode=episode,
                ambiguous=len(matches) > 1,
                coordinate_disagrees=(
                    episode is not None
                    and episode.number is not None
                    and parse.episodes != (episode.number,)
                ),
            )
        )

    exact = tuple(
        observation for observation in observations if observation.episode is not None
    )
    identities = tuple(observation.episode.identity for observation in exact if observation.episode)
    one_to_one = len(identities) == len(set(identities))
    eligible_count = len(observations)
    exact_match_count = len(exact)
    ambiguous_count = sum(observation.ambiguous for observation in observations)
    disagreement_count = sum(
        observation.coordinate_disagrees for observation in observations
    )
    proven = (
        eligible_count >= _MIN_EXACT_MATCHES
        and exact_match_count >= _MIN_EXACT_MATCHES
        and exact_match_count * 2 >= eligible_count
        and disagreement_count >= _MIN_COORDINATE_DISAGREEMENTS
        and ambiguous_count == 0
        and one_to_one
    )
    return SegmentCountedTitleAnalysis(
        observations=tuple(observations),
        eligible_count=eligible_count,
        exact_match_count=exact_match_count,
        ambiguous_count=ambiguous_count,
        coordinate_disagreement_count=disagreement_count,
        one_to_one=one_to_one,
        proven=proven,
        reasons=(
            f"segment-counted-title-eligible:{eligible_count}",
            f"segment-counted-title-exact-matches:{exact_match_count}/{eligible_count}",
            f"segment-counted-title-ambiguous:{ambiguous_count}",
            f"segment-counted-title-coordinate-disagreements:{disagreement_count}",
            f"segment-counted-title-one-to-one:{str(one_to_one).casefold()}",
            f"segment-counted-title-compatible:{str(proven).casefold()}",
        ),
    )
