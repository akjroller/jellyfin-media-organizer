from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from .models import CandidateEvidence, ParseResult, ProviderIdentity
from .providers import MetadataProvider, ProviderEpisodeCatalog
from .segment_counted_titles import (
    SegmentCountedTitleAnalysis,
    analyze_segment_counted_titles,
    is_segment_counted_title_candidate,
)

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


def _single_token_prefix_expansion_equivalent(source: str, candidate: str) -> bool:
    source_tokens = _normalize(source).split()
    candidate_tokens = _normalize(candidate).split()
    if len(source_tokens) < 2 or len(source_tokens) != len(candidate_tokens):
        return False

    differences = tuple(
        (source_token, candidate_token)
        for source_token, candidate_token in zip(
            source_tokens, candidate_tokens, strict=True
        )
        if source_token != candidate_token
    )
    if len(differences) != 1:
        return False

    source_token, candidate_token = differences[0]
    return (
        source_token.isalpha()
        and candidate_token.isalpha()
        and len(source_token) >= 3
        and len(candidate_token) >= len(source_token) + 3
        and candidate_token.startswith(source_token)
    )


def _provider_subtitle_prefix(source: str, candidate_title: str) -> bool:
    """Return true when a multi-token source exactly prefixes a colon subtitle."""

    candidate = unicodedata.normalize("NFKC", candidate_title)
    head, separator, tail = candidate.partition(":")
    if not separator or not tail.strip():
        return False
    source_normalized = _normalize(source)
    if len(source_normalized.split()) < 2:
        return False
    return _normalize(head) == source_normalized


def _provider_ampersand_equivalent(source: str, candidate_title: str) -> bool:
    """Return true for a complete-title ``and`` versus ``&`` equivalence."""

    source_normalized = _normalize(source)
    if "and" not in source_normalized.split():
        return False
    candidate = unicodedata.normalize("NFKC", candidate_title)
    if "&" not in candidate:
        return False
    candidate_with_conjunction = candidate.replace("&", " and ")
    return _normalize(candidate_with_conjunction) == source_normalized


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
        if _provider_ampersand_equivalent(normalized, candidate_title):
            return 0.90, ("provider-ampersand-equivalent",)
        if _single_token_prefix_expansion_equivalent(normalized, candidate_normalized):
            return 0.78, ("single-token-prefix-expansion-equivalent",)

        source_tokens = normalized.split()
        if _provider_subtitle_prefix(normalized, candidate_title):
            return 0.78, ("provider-subtitle-prefix",)

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
    """Collect clean aired coordinates while ignoring independent numbering families."""

    coordinates: set[tuple[int, int]] = set()
    for parse in parses:
        has_aired = parse.season is not None or bool(parse.episodes)
        if not has_aired:
            continue

        has_other_numbering = any(
            (
                parse.absolute_episode is not None,
                parse.special_episode is not None,
                parse.episode_date is not None,
                parse.segment_hint is not None,
            )
        )
        if parse.season is None or not parse.episodes or has_other_numbering:
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


def _mixed_segment_title_observations(
    parses: tuple[ParseResult, ...],
) -> tuple[str, ...]:
    """Collect exact segment titles only from a genuinely mixed aired/segment group."""

    segment_titles: list[str] = []
    has_plain_aired = False
    for parse in parses:
        if parse.segment_hint is None:
            if (
                parse.season is not None
                and bool(parse.episodes)
                and parse.absolute_episode is None
                and parse.special_episode is None
                and parse.episode_date is None
            ):
                has_plain_aired = True
            continue

        if any(
            (
                parse.absolute_episode is not None,
                parse.special_episode is not None,
                parse.episode_date is not None,
            )
        ):
            return ()
        if (
            parse.season is None
            or len(parse.episodes) != 1
            or parse.title_hint is None
            or not parse.title_hint.strip()
        ):
            return ()
        title = _clean_title_hint(parse.title_hint)
        if not title:
            return ()
        segment_titles.append(title)

    if not has_plain_aired or len(segment_titles) < _MIN_TITLE_OBSERVATIONS:
        return ()
    if len(set(segment_titles)) != len(segment_titles):
        return ()
    return tuple(sorted(segment_titles))


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
        for identity, compatible in sorted(
            outcomes.items(), key=lambda item: item[0].key
        )
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


def catalog_coordinate_title_rescue(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
) -> StructuralCatalogDecision | None:
    """Confirm one borderline aired source by exact title at the same coordinate."""

    if len(parses) != 1:
        return None
    parse = parses[0]
    if (
        parse.season is None
        or len(parse.episodes) != 1
        or parse.title_hint is None
        or not parse.title_hint.strip()
        or parse.segment_hint is not None
        or parse.absolute_episode is not None
        or parse.special_episode is not None
        or parse.episode_date is not None
    ):
        return None

    observations = _title_observations(parses)
    contenders = tuple(
        candidate
        for candidate in ranked
        if candidate.score >= _MIN_CATALOG_RESCUE_SCORE
    )
    if not observations or not contenders:
        return None

    outcomes: dict[ProviderIdentity, bool | None] = {}
    extra_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    for candidate in contenders:
        catalog = provider.episode_catalog(candidate.provider_identity)
        request_reason = f"catalog-coordinate-title-request:{catalog.request_key}"
        if not catalog.resolved or catalog.errors:
            outcomes[candidate.provider_identity] = None
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                "catalog-coordinate-title-rescue:indeterminate-catalog",
                *(
                    f"catalog-coordinate-title-error:{error}"
                    for error in catalog.errors
                ),
            )
            continue

        by_coordinate = _catalog_coordinate_map(catalog)
        compatible = True
        reasons: list[str] = [request_reason]
        for season, episode, observed_title in observations:
            titles = by_coordinate.get((season, episode), ())
            if len(titles) != 1 or titles[0] != observed_title:
                compatible = False
                reasons.append(
                    f"catalog-coordinate-title-mismatch:S{season:02d}E{episode:02d}"
                )
                break
        reasons.append(
            f"catalog-coordinate-title-compatible:{str(compatible).casefold()}"
        )
        outcomes[candidate.provider_identity] = compatible
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
            reasons=(
                "catalog-coordinate-title-rescue:indeterminate-candidate-catalog",
            ),
        )

    winners = tuple(
        identity
        for identity, compatible in sorted(
            outcomes.items(), key=lambda item: item[0].key
        )
        if compatible
    )
    if len(winners) != 1:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("catalog-coordinate-title-rescue:no-unique-compatible-candidate",),
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
            "catalog-coordinate-title-rescue:unique-compatible-candidate",
            f"catalog-coordinate-title-rescue-winner:{winner.key}",
        ),
    )


def _mixed_segment_title_rescue(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
) -> StructuralCatalogDecision | None:
    titles = _mixed_segment_title_observations(parses)
    if len(titles) < _MIN_TITLE_OBSERVATIONS:
        return None

    match_counts: dict[ProviderIdentity, int | None] = {}
    exact_compatibility: dict[ProviderIdentity, bool] = {}
    partial_qualification: dict[ProviderIdentity, bool] = {}
    extra_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    for candidate in sorted(ranked, key=lambda item: item.provider_identity.key):
        catalog = provider.episode_catalog(candidate.provider_identity)
        request_reason = f"mixed-segment-title-request:{catalog.request_key}"
        if not catalog.resolved:
            match_counts[candidate.provider_identity] = None
            exact_compatibility[candidate.provider_identity] = False
            partial_qualification[candidate.provider_identity] = False
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                "mixed-segment-title-rescue:indeterminate-catalog",
            )
            continue
        if catalog.errors:
            match_counts[candidate.provider_identity] = None
            exact_compatibility[candidate.provider_identity] = False
            partial_qualification[candidate.provider_identity] = False
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                *(f"mixed-segment-title-error:{error}" for error in catalog.errors),
            )
            continue

        by_title: dict[str, list[ProviderIdentity]] = {}
        for episode in catalog.episodes:
            title = _normalize(episode.title)
            if title:
                by_title.setdefault(title, []).append(episode.identity)

        selected: list[ProviderIdentity] = []
        missing = 0
        ambiguous = 0
        reasons: list[str] = [request_reason]
        for title in titles:
            matches = tuple(by_title.get(title, ()))
            if len(matches) == 1:
                selected.append(matches[0])
            elif not matches:
                missing += 1
                reasons.append(f"mixed-segment-title-missing:{title}")
            else:
                ambiguous += 1
                reasons.append(f"mixed-segment-title-ambiguous:{title}")

        if len(set(selected)) != len(selected):
            match_counts[candidate.provider_identity] = 0
            exact_compatibility[candidate.provider_identity] = False
            partial_qualification[candidate.provider_identity] = False
            reasons.extend(
                (
                    "mixed-segment-title-distinct-titles-collapse",
                    f"mixed-segment-title-exact-matches:0/{len(titles)}",
                    "mixed-segment-title-compatible:false",
                    "mixed-segment-title-partial-qualified:false",
                )
            )
            extra_reasons[candidate.provider_identity] = tuple(reasons)
            continue

        exact_matches = len(selected)
        exact = missing == 0 and ambiguous == 0 and exact_matches == len(titles)
        partial = not exact and exact_matches >= 3 and exact_matches * 2 >= len(titles)
        match_counts[candidate.provider_identity] = exact_matches
        exact_compatibility[candidate.provider_identity] = exact
        partial_qualification[candidate.provider_identity] = partial
        reasons.extend(
            (
                f"mixed-segment-title-exact-matches:{exact_matches}/{len(titles)}",
                f"mixed-segment-title-compatible:{str(exact).casefold()}",
                f"mixed-segment-title-partial-qualified:{str(partial).casefold()}",
            )
        )
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
    if any(value is None for value in match_counts.values()):
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("mixed-segment-title-rescue:indeterminate-candidate-catalog",),
        )

    exact_winners = tuple(
        identity
        for identity, compatible in sorted(
            exact_compatibility.items(), key=lambda item: item[0].key
        )
        if compatible
    )
    if len(exact_winners) > 1:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("mixed-segment-title-rescue:no-unique-compatible-candidate",),
        )

    partial_winner = False
    if exact_winners:
        winner = exact_winners[0]
    else:
        qualified = tuple(
            identity
            for identity, is_qualified in sorted(
                partial_qualification.items(), key=lambda item: item[0].key
            )
            if is_qualified
        )
        if not qualified:
            return StructuralCatalogDecision(
                winner=None,
                candidates=enriched,
                reasons=("mixed-segment-title-rescue:no-unique-compatible-candidate",),
            )

        best_count = max(match_counts[identity] or 0 for identity in qualified)
        best = tuple(
            identity
            for identity in qualified
            if (match_counts[identity] or 0) == best_count
        )
        if len(best) != 1:
            return StructuralCatalogDecision(
                winner=None,
                candidates=enriched,
                reasons=("mixed-segment-title-rescue:no-unique-compatible-candidate",),
            )
        winner = best[0]
        runner_up_count = max(
            (
                count or 0
                for identity, count in match_counts.items()
                if identity != winner
            ),
            default=0,
        )
        if best_count - runner_up_count < 2:
            return StructuralCatalogDecision(
                winner=None,
                candidates=enriched,
                reasons=("mixed-segment-title-rescue:partial-margin-insufficient",),
            )
        partial_winner = True

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
            (
                "mixed-segment-title-rescue:unique-partial-candidate"
                if partial_winner
                else "mixed-segment-title-rescue:unique-compatible-candidate"
            ),
            f"mixed-segment-title-rescue-winner:{winner.key}",
        ),
    )


def aired_catalog_rescue(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
) -> StructuralCatalogDecision | None:
    """Rescue low-text-confidence aired groups from strong catalog evidence."""

    if not ranked or ranked[0].score < _MIN_CATALOG_RESCUE_SCORE:
        return None
    coordinates = _aired_coordinates(parses)
    if len(coordinates) < _MIN_COORDINATE_OBSERVATIONS:
        return _mixed_segment_title_rescue(provider, parses, ranked)

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
        for identity, compatible in sorted(
            outcomes.items(), key=lambda item: item[0].key
        )
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


def segment_counted_title_rescue(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
    *,
    minimum_gap: float,
    suspicious_threshold: float,
) -> StructuralCatalogDecision | None:
    """Resolve a same-title show tie only after repeated exact title proof."""

    eligible = tuple(
        parse for parse in parses if is_segment_counted_title_candidate(parse)
    )
    if len(eligible) < 3:
        return None
    if len(ranked) < 2 or ranked[0].score < suspicious_threshold:
        return None
    top_score = ranked[0].score
    contenders = tuple(
        candidate for candidate in ranked if top_score - candidate.score < minimum_gap
    )
    if len(contenders) < 2:
        return None

    analyses: dict[ProviderIdentity, SegmentCountedTitleAnalysis] = {}
    indeterminate: set[ProviderIdentity] = set()
    extra_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    for candidate in contenders:
        catalog = provider.episode_catalog(candidate.provider_identity)
        request_reason = f"segment-counted-title-request:{catalog.request_key}"
        if not catalog.resolved:
            indeterminate.add(candidate.provider_identity)
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                "segment-counted-title-rescue:indeterminate-catalog",
            )
            continue
        if catalog.errors:
            indeterminate.add(candidate.provider_identity)
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                *(f"segment-counted-title-error:{error}" for error in catalog.errors),
            )
            continue
        analysis = analyze_segment_counted_titles(parses, catalog)
        analyses[candidate.provider_identity] = analysis
        extra_reasons[candidate.provider_identity] = (
            request_reason,
            *analysis.reasons,
        )

    triggered = {
        identity for identity, analysis in analyses.items() if analysis.triggered
    }
    if not triggered:
        return None

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
    if indeterminate:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("segment-counted-title-rescue:indeterminate-candidate-catalog",),
        )

    proven = tuple(
        identity
        for identity in sorted(triggered, key=lambda item: item.key)
        if analyses[identity].proven
    )
    if len(triggered) != 1 or len(proven) != 1:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("segment-counted-title-rescue:no-unique-safe-candidate",),
        )

    winner = proven[0]
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
            "segment-counted-title-rescue:unique-compatible-candidate",
            f"segment-counted-title-rescue-winner:{winner.key}",
        ),
    )
