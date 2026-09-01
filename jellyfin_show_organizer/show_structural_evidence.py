from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from .models import CandidateEvidence, ParseResult, ProviderIdentity
from .providers import MetadataProvider, ProviderEpisodeCatalog

_MIN_CATALOG_RESCUE_SCORE = 0.60
_MIN_TITLE_OBSERVATIONS = 2
_MIN_COORDINATE_OBSERVATIONS = 2
_TRAILING_TITLE_NOISE = re.compile(
    r"(?i)(?:[ ._-]+(?:repack|proper|v\d+|ntsc|pal|dvd|bd))+$"
)


@dataclass(frozen=True, slots=True)
class StructuralCatalogDecision:
    winner: ProviderIdentity | None
    candidates: tuple[CandidateEvidence, ...]
    reasons: tuple[str, ...]


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def token_merge_queries(value: str) -> tuple[str, ...]:
    """Return a single two-token compaction query for conservative discovery."""

    tokens = re.findall(r"\w+", unicodedata.normalize("NFKC", value), flags=re.UNICODE)
    if len(tokens) != 2:
        return ()
    merged = "".join(tokens)
    return (merged,) if len(merged) >= 4 else ()


def _initialism_equivalent(source: str, candidate: str) -> bool:
    source_tokens = _normalize(source).split()
    candidate_tokens = _normalize(candidate).split()
    if not source_tokens or not candidate_tokens:
        return False

    source_index = 0
    candidate_index = 0
    used_initialism = False
    while source_index < len(source_tokens) and candidate_index < len(candidate_tokens):
        source_token = source_tokens[source_index]
        if source_token == candidate_tokens[candidate_index]:
            source_index += 1
            candidate_index += 1
            continue

        if not source_token.isalpha() or not 2 <= len(source_token) <= 6:
            return False

        matched = False
        max_end = min(len(candidate_tokens), candidate_index + len(source_token) + 2)
        for end in range(candidate_index + 2, max_end + 1):
            span = candidate_tokens[candidate_index:end]
            if any(not token or not token[0].isalpha() for token in span):
                continue
            initials = "".join(token[0] for token in span)
            if initials == source_token:
                source_index += 1
                candidate_index = end
                used_initialism = True
                matched = True
                break
        if not matched:
            return False

    return (
        used_initialism
        and source_index == len(source_tokens)
        and candidate_index == len(candidate_tokens)
    )


def structural_title_score(
    identities: tuple[str, ...],
    candidate_title: str,
) -> tuple[float | None, tuple[str, ...]]:
    """Return strong structural title evidence without fuzzy typo guessing."""

    candidate_normalized = _normalize(candidate_title)
    candidate_tokens = candidate_normalized.split()
    for identity in identities:
        normalized = _normalize(identity)
        if not normalized:
            continue
        if _initialism_equivalent(normalized, candidate_normalized):
            return 0.90, ("token-initialism-equivalent",)

        source_tokens = normalized.split()
        compact_source = "".join(source_tokens)
        compact_candidate = "".join(candidate_tokens)
        if (
            len(source_tokens) >= 2
            and compact_source == compact_candidate
            and normalized != candidate_normalized
        ):
            return 0.78, ("token-boundary-equivalent",)
        if (
            len(source_tokens) >= 2
            and candidate_tokens
            and candidate_tokens[0] == compact_source
            and len(candidate_tokens) > 1
        ):
            return 0.78, ("compacted-source-prefix",)
    return None, ()


def _clean_title_hint(value: str) -> str:
    cleaned = _TRAILING_TITLE_NOISE.sub("", value).strip(" ._-")
    return _normalize(cleaned)


def _aired_coordinates(parses: tuple[ParseResult, ...]) -> tuple[tuple[int, int], ...]:
    coordinates: set[tuple[int, int]] = set()
    for parse in parses:
        if (
            parse.season is None
            or not parse.episodes
            or parse.absolute_episode is not None
            or parse.special_episode is not None
            or parse.episode_date is not None
            or parse.segment_hint is not None
        ):
            return ()
        coordinates.update((parse.season, episode) for episode in parse.episodes)
    return tuple(sorted(coordinates))


def _title_observations(
    parses: tuple[ParseResult, ...],
) -> tuple[tuple[int, int, str], ...]:
    observations: dict[tuple[int, int], str] = {}
    for parse in parses:
        if (
            parse.season is None
            or len(parse.episodes) != 1
            or parse.title_hint is None
            or not parse.title_hint.strip()
        ):
            continue
        title = _clean_title_hint(parse.title_hint)
        if not title:
            continue
        coordinate = (parse.season, parse.episodes[0])
        previous = observations.get(coordinate)
        if previous is not None and previous != title:
            return ()
        observations[coordinate] = title
    return tuple(
        (season, episode, title)
        for (season, episode), title in sorted(observations.items())
    )


def _catalog_coordinate_map(
    catalog: ProviderEpisodeCatalog,
) -> dict[tuple[int, int], tuple[str, ...]]:
    values: dict[tuple[int, int], list[str]] = {}
    for episode in catalog.episodes:
        if episode.number is None:
            continue
        values.setdefault((episode.season, episode.number), []).append(
            _normalize(episode.title)
        )
    return {coordinate: tuple(titles) for coordinate, titles in values.items()}


def catalog_title_tiebreak(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
    *,
    minimum_gap: float,
    suspicious_threshold: float,
) -> StructuralCatalogDecision | None:
    """Break an exact-title/near-title tie only with multiple exact episode titles."""

    if len(ranked) < 2 or ranked[0].score < suspicious_threshold:
        return None
    top_score = ranked[0].score
    contenders = tuple(
        candidate for candidate in ranked if top_score - candidate.score < minimum_gap
    )
    if len(contenders) < 2:
        return None

    observations = _title_observations(parses)
    if len(observations) < _MIN_TITLE_OBSERVATIONS:
        return None

    outcomes: dict[ProviderIdentity, bool | None] = {}
    extra_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    for candidate in contenders:
        catalog = provider.episode_catalog(candidate.provider_identity)
        request_reason = f"catalog-title-request:{catalog.request_key}"
        if not catalog.resolved:
            outcomes[candidate.provider_identity] = None
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                "catalog-title-tiebreak:indeterminate-catalog",
            )
            continue
        if catalog.errors:
            outcomes[candidate.provider_identity] = None
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                *(f"catalog-title-error:{error}" for error in catalog.errors),
            )
            continue

        by_coordinate = _catalog_coordinate_map(catalog)
        matched = True
        reasons: list[str] = [request_reason]
        for season, episode, observed_title in observations:
            titles = by_coordinate.get((season, episode), ())
            if len(titles) != 1 or titles[0] != observed_title:
                matched = False
                reasons.append(f"catalog-title-mismatch:S{season:02d}E{episode:02d}")
                break
        reasons.append(f"catalog-title-compatible:{str(matched).casefold()}")
        outcomes[candidate.provider_identity] = matched
        extra_reasons[candidate.provider_identity] = tuple(reasons)

    enriched = tuple(
        replace(
            candidate,
            reasons=(
                *candidate.reasons,
                *extra_reasons.get(candidate.provider_identity, ()),
            ),
        )
        for candidate in ranked
    )
    if any(value is None for value in outcomes.values()):
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("catalog-title-tiebreak:indeterminate-candidate-catalog",),
        )

    winners = tuple(
        identity
        for identity, compatible in sorted(outcomes.items(), key=lambda item: item[0].key)
        if compatible
    )
    if len(winners) != 1:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("catalog-title-tiebreak:no-unique-compatible-candidate",),
        )

    winner = winners[0]
    winner_first = tuple(
        sorted(
            enriched,
            key=lambda candidate: (
                candidate.provider_identity != winner,
                -candidate.score,
                candidate.provider_identity.key,
            ),
        )
    )
    return StructuralCatalogDecision(
        winner=winner,
        candidates=winner_first,
        reasons=(
            "catalog-title-tiebreak:unique-compatible-candidate",
            f"catalog-tiebreak-winner:{winner.key}",
        ),
    )


def aired_catalog_rescue(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
) -> StructuralCatalogDecision | None:
    """Rescue low-text-confidence aired groups only from multiple coordinates."""

    if not ranked or ranked[0].score < _MIN_CATALOG_RESCUE_SCORE:
        return None
    coordinates = _aired_coordinates(parses)
    if len(coordinates) < _MIN_COORDINATE_OBSERVATIONS:
        return None

    outcomes: dict[ProviderIdentity, bool | None] = {}
    extra_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    for candidate in sorted(ranked, key=lambda item: item.provider_identity.key):
        catalog = provider.episode_catalog(candidate.provider_identity)
        request_reason = f"aired-catalog-rescue-request:{catalog.request_key}"
        if not catalog.resolved:
            outcomes[candidate.provider_identity] = None
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                "aired-catalog-rescue:indeterminate-catalog",
            )
            continue
        if catalog.errors:
            outcomes[candidate.provider_identity] = None
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                *(f"aired-catalog-rescue-error:{error}" for error in catalog.errors),
            )
            continue

        available = {
            (episode.season, episode.number)
            for episode in catalog.episodes
            if episode.number is not None
        }
        compatible = all(coordinate in available for coordinate in coordinates)
        outcomes[candidate.provider_identity] = compatible
        extra_reasons[candidate.provider_identity] = (
            request_reason,
            f"aired-catalog-rescue-compatible:{str(compatible).casefold()}",
        )

    enriched = tuple(
        replace(
            candidate,
            reasons=(
                *candidate.reasons,
                *extra_reasons.get(candidate.provider_identity, ()),
            ),
        )
        for candidate in ranked
    )
    if any(value is None for value in outcomes.values()):
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("aired-catalog-rescue:indeterminate-candidate-catalog",),
        )

    winners = tuple(
        identity
        for identity, compatible in sorted(outcomes.items(), key=lambda item: item[0].key)
        if compatible
    )
    if len(winners) != 1:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("aired-catalog-rescue:no-unique-compatible-candidate",),
        )

    winner = winners[0]
    winner_first = tuple(
        sorted(
            enriched,
            key=lambda candidate: (
                candidate.provider_identity != winner,
                -candidate.score,
                candidate.provider_identity.key,
            ),
        )
    )
    return StructuralCatalogDecision(
        winner=winner,
        candidates=winner_first,
        reasons=(
            "aired-catalog-rescue:unique-compatible-candidate",
            f"aired-catalog-rescue-winner:{winner.key}",
        ),
    )
