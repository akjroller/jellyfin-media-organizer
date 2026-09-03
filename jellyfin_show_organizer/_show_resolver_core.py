from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import StrEnum

from .models import (
    CandidateEvidence,
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    ParseResult,
    ProviderIdentity,
    TitlePreference,
)
from .numbering_inference import infer_group_numbering_mode
from .overrides import OverrideCatalog, ShowOverride
from .provider_aliases import TvmazeAliasProviderAdapter
from .providers import MetadataProvider, ProviderEpisodeCatalog, ProviderShow
from .show_alias_evidence import (
    catalog_group_rescue,
    enrich_provider_alias_evidence,
)
from .show_structural_evidence import (
    aired_catalog_rescue,
    catalog_coordinate_title_rescue,
    catalog_title_tiebreak,
    segment_counted_title_rescue,
    structural_title_score,
    token_merge_queries,
)
from .tvmaze_cache import JsonGetter, TvmazeCatalogCache

_MATCH_THRESHOLD = 0.90
_SUSPICIOUS_THRESHOLD = 0.75
_MINIMUM_MATCH_GAP = 0.08
_SEARCH_BACKOFF_STOPWORDS = frozenset(
    {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to"}
)


class ResolutionStatus(StrEnum):
    MATCHED = "matched"
    SUSPICIOUS = "suspicious"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ShowResolution:
    status: ResolutionStatus
    show: CanonicalShow | None
    evidence: MatchEvidence


@dataclass(frozen=True, slots=True)
class _ObservedEpisodeEvidence:
    mode: NumberingMode
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CatalogTieBreak:
    winner: ProviderIdentity | None
    candidates: tuple[CandidateEvidence, ...]
    reasons: tuple[str, ...]


def normalize_show_identity(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    ).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _search_backoff_titles(value: str) -> tuple[str, ...]:
    """Return deterministic meaningful title prefixes for candidate discovery.

    Backoff changes only the provider query used to discover candidates. It never
    lowers the evidence threshold or directly selects a show.
    """

    normalized = unicodedata.normalize("NFKC", value)
    tokens = re.findall(r"\w+", normalized, flags=re.UNICODE)
    if len(tokens) < 2:
        return ()

    original_identity = normalize_show_identity(value)
    seen: set[str] = {original_identity}
    candidates: list[str] = []
    for end in range(len(tokens) - 1, 0, -1):
        prefix = " ".join(tokens[:end]).strip()
        identity = normalize_show_identity(prefix)
        if identity in seen or len(identity) < 4:
            continue
        meaningful = tuple(
            token
            for token in identity.split()
            if token not in _SEARCH_BACKOFF_STOPWORDS
        )
        if not meaningful:
            continue
        seen.add(identity)
        candidates.append(prefix)
    return tuple(candidates)


def _source_titles(parses: Iterable[ParseResult]) -> tuple[str, ...]:
    values = [
        parse.series_hint.strip()
        for parse in parses
        if parse.series_hint is not None and parse.series_hint.strip()
    ]
    return tuple(
        sorted(values, key=lambda value: (normalize_show_identity(value), value))
    )


def _representative_title(titles: tuple[str, ...]) -> str | None:
    if not titles:
        return None

    normalized_counts = Counter(normalize_show_identity(title) for title in titles)
    representative_identity = min(
        normalized_counts,
        key=lambda identity: (-normalized_counts[identity], identity),
    )
    display_counts = Counter(
        title
        for title in titles
        if normalize_show_identity(title) == representative_identity
    )
    return min(
        display_counts,
        key=lambda title: (-display_counts[title], title.casefold(), title),
    )


def _consistent_year(parses: Iterable[ParseResult]) -> tuple[int | None, bool]:
    years = {parse.year for parse in parses if parse.year is not None}
    if len(years) > 1:
        return None, False
    return (next(iter(years)) if years else None), True


def _matching_overrides(
    source_key: str,
    titles: tuple[str, ...],
    catalog: OverrideCatalog,
) -> tuple[ShowOverride, ...]:
    identities = {normalize_show_identity(source_key)}
    identities.update(normalize_show_identity(title) for title in titles)

    matches = []
    for show in catalog.shows:
        show_identities = {normalize_show_identity(show.key)}
        show_identities.update(normalize_show_identity(alias) for alias in show.aliases)
        if show.preferred_title:
            show_identities.add(normalize_show_identity(show.preferred_title))
        if identities & show_identities:
            matches.append(show)
    return tuple(sorted(matches, key=lambda show: show.key.casefold()))


def _explicit_identities(
    parses: Iterable[ParseResult],
    override: ShowOverride | None,
) -> tuple[ProviderIdentity, ...]:
    values: set[ProviderIdentity] = set()
    for parse in parses:
        values.update(parse.provider_identities)
    if override is not None and override.provider_identity is not None:
        values.add(override.provider_identity)
    return tuple(sorted(values, key=lambda identity: identity.key))


def _preferred_title(
    override: ShowOverride | None,
    source_title: str | None,
    provider_title: str | None,
) -> str | None:
    if override is None:
        return provider_title or source_title
    if override.title_preference is TitlePreference.OVERRIDE:
        return override.preferred_title
    if override.title_preference is TitlePreference.SOURCE:
        return source_title or override.preferred_title or provider_title
    return provider_title or override.preferred_title or source_title


def _numbering_mode(override: ShowOverride | None) -> NumberingMode:
    return override.numbering_mode if override is not None else NumberingMode.AIRED


def _unresolved(method: str, *reasons: str) -> ShowResolution:
    return ShowResolution(
        status=ResolutionStatus.UNRESOLVED,
        show=None,
        evidence=MatchEvidence(
            method=method,
            confidence=0.0,
            reasons=tuple(reasons),
        ),
    )


def _score_candidate(
    candidate: ProviderShow,
    identities: tuple[str, ...],
    year_hint: int | None,
) -> CandidateEvidence:
    normalized_title = normalize_show_identity(candidate.title)
    ratios = [
        SequenceMatcher(None, identity, normalized_title).ratio()
        for identity in identities
        if identity
    ]
    best_ratio = max(ratios, default=0.0)
    reasons: list[str] = []

    if normalized_title in identities:
        score = 0.90
        reasons.append("exact-normalized-title")
    else:
        structural_score, structural_reasons = structural_title_score(
            identities,
            candidate.title,
        )
        fuzzy_score = 0.72 * best_ratio
        if structural_score is not None and structural_score > fuzzy_score:
            score = structural_score
            reasons.extend(structural_reasons)
            reasons.append(f"title-similarity:{best_ratio:.3f}")
        else:
            score = fuzzy_score
            reasons.append(f"title-similarity:{best_ratio:.3f}")

    if year_hint is not None:
        if candidate.year == year_hint:
            score = min(1.0, score + 0.10)
            reasons.append("year-match")
        elif candidate.year is None:
            score = min(score, 0.79)
            reasons.append("candidate-year-missing")
        else:
            score = min(score * 0.45, 0.45)
            reasons.append("year-mismatch")

    return CandidateEvidence(
        provider_identity=candidate.identity,
        title=candidate.title,
        score=round(score, 6),
        reasons=tuple(reasons),
    )


def _explicit_conflict_reasons(
    identities: tuple[ProviderIdentity, ...],
) -> tuple[str, ...]:
    if identities and all(identity.provider == "tvmaze" for identity in identities):
        return (
            "conflicting-explicit-tvmaze-ids",
            "conflicting-explicit-provider-identities",
        )
    return ("conflicting-explicit-provider-identities",)


def _parse_numbering_family(parse: ParseResult) -> str | None:
    families = [
        family
        for family, present in (
            ("aired", parse.season is not None or bool(parse.episodes)),
            ("absolute", parse.absolute_episode is not None),
            ("special", parse.special_episode is not None),
            ("date", parse.episode_date is not None),
            ("segment", parse.segment_hint is not None),
        )
        if present
    ]
    if len(families) != 1:
        return None
    return families[0]


def _observed_episode_evidence(
    parses: tuple[ParseResult, ...],
    mode: NumberingMode,
) -> _ObservedEpisodeEvidence | None:
    expected_family = {
        NumberingMode.AIRED: "aired",
        NumberingMode.ABSOLUTE: "absolute",
        NumberingMode.PARENTHESIZED_ABSOLUTE: "absolute",
        NumberingMode.SPECIAL: "special",
        NumberingMode.DATE: "date",
        NumberingMode.SEGMENT_TITLE: "segment",
    }[mode]
    values: set[str] = set()

    for parse in parses:
        family = _parse_numbering_family(parse)
        if family is None:
            if any(
                (
                    parse.season is not None,
                    bool(parse.episodes),
                    parse.absolute_episode is not None,
                    parse.special_episode is not None,
                    parse.episode_date is not None,
                    parse.segment_hint is not None,
                )
            ):
                return None
            continue
        if family != expected_family:
            return None

        if mode is NumberingMode.AIRED:
            if parse.season is None or not parse.episodes:
                return None
            values.update(
                f"S{parse.season:02d}E{episode:02d}" for episode in parse.episodes
            )
        elif mode in {NumberingMode.ABSOLUTE, NumberingMode.PARENTHESIZED_ABSOLUTE}:
            if parse.absolute_episode is None or parse.absolute_episode <= 0:
                return None
            values.add(str(parse.absolute_episode))
        elif mode is NumberingMode.SPECIAL:
            if parse.special_kind is None or parse.special_episode is None:
                return None
            values.add(str(parse.special_episode))
        elif mode is NumberingMode.DATE:
            if parse.episode_date is None:
                return None
            values.add(parse.episode_date)
        else:
            if parse.title_hint is None or not parse.title_hint.strip():
                return None
            values.add(normalize_show_identity(parse.title_hint))

    if not values:
        return None
    return _ObservedEpisodeEvidence(mode=mode, values=tuple(sorted(values)))


def _catalog_compatibility_reasons(
    catalog: ProviderEpisodeCatalog,
    observed: _ObservedEpisodeEvidence,
) -> tuple[bool | None, tuple[str, ...]]:
    request_reason = f"catalog-request:{catalog.request_key}"
    if not catalog.resolved:
        return None, (
            request_reason,
            "catalog-unresolved:"
            f"{catalog.unresolved_reason or 'provider-catalog-unresolved'}",
        )
    if catalog.errors:
        return None, (
            request_reason,
            *(f"catalog-error:{error}" for error in catalog.errors),
        )

    episodes = catalog.episodes
    mode = observed.mode

    if mode is NumberingMode.AIRED:
        available = {
            f"S{episode.season:02d}E{episode.number:02d}"
            for episode in episodes
            if episode.number is not None
        }
    elif mode in {NumberingMode.ABSOLUTE, NumberingMode.PARENTHESIZED_ABSOLUTE}:
        regular = tuple(
            sorted(
                (
                    episode
                    for episode in episodes
                    if episode.season > 0 and episode.number is not None
                ),
                key=lambda episode: (
                    episode.season,
                    episode.number,
                    episode.identity.key,
                ),
            )
        )
        available = {str(index) for index, _episode in enumerate(regular, start=1)}
    elif mode is NumberingMode.SPECIAL:
        available = {
            str(episode.number)
            for episode in episodes
            if episode.number is not None
            and (
                episode.season == 0
                or (
                    episode.episode_type is not None
                    and episode.episode_type != "regular"
                )
            )
        }
    elif mode is NumberingMode.DATE:
        available = {
            episode.airdate for episode in episodes if episode.airdate is not None
        }
    else:
        available = {normalize_show_identity(episode.title) for episode in episodes}

    missing = tuple(value for value in observed.values if value not in available)
    compatible = not missing
    return compatible, (
        request_reason,
        f"catalog-compatible:{str(compatible).casefold()}:{mode.value}",
        *(f"catalog-missing:{value}" for value in missing),
    )


def _catalog_tie_break(
    parses: tuple[ParseResult, ...],
    mode: NumberingMode,
    provider: MetadataProvider,
    ranked: tuple[CandidateEvidence, ...],
) -> _CatalogTieBreak | None:
    if len(ranked) < 2 or ranked[0].score < _SUSPICIOUS_THRESHOLD:
        return None

    top_score = ranked[0].score
    contenders = tuple(
        candidate
        for candidate in ranked
        if top_score - candidate.score < _MINIMUM_MATCH_GAP
    )
    if len(contenders) < 2:
        return None

    observed = _observed_episode_evidence(parses, mode)
    if observed is None:
        return None

    compatibility: dict[ProviderIdentity, bool | None] = {}
    catalog_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    for candidate in contenders:
        catalog = provider.episode_catalog(candidate.provider_identity)
        compatible, reasons = _catalog_compatibility_reasons(catalog, observed)
        compatibility[candidate.provider_identity] = compatible
        catalog_reasons[candidate.provider_identity] = reasons

    enriched = tuple(
        replace(
            candidate,
            reasons=(
                *candidate.reasons,
                *catalog_reasons.get(candidate.provider_identity, ()),
            ),
        )
        for candidate in ranked
    )

    if any(value is None for value in compatibility.values()):
        return _CatalogTieBreak(
            winner=None,
            candidates=enriched,
            reasons=(
                f"catalog-tiebreak-mode:{mode.value}",
                "catalog-tiebreak:indeterminate-candidate-catalog",
            ),
        )

    compatible_identities = tuple(
        identity
        for identity, value in sorted(
            compatibility.items(), key=lambda item: item[0].key
        )
        if value
    )
    if len(compatible_identities) != 1:
        return _CatalogTieBreak(
            winner=None,
            candidates=enriched,
            reasons=(
                f"catalog-tiebreak-mode:{mode.value}",
                "catalog-tiebreak:no-unique-compatible-candidate",
            ),
        )

    winner = compatible_identities[0]
    winner_first = tuple(
        sorted(
            enriched,
            key=lambda candidate: (
                candidate.provider_identity != winner,
                -candidate.score,
                normalize_show_identity(candidate.title),
                candidate.provider_identity.key,
            ),
        )
    )
    return _CatalogTieBreak(
        winner=winner,
        candidates=winner_first,
        reasons=(
            f"catalog-tiebreak-mode:{mode.value}",
            "catalog-tiebreak:unique-compatible-candidate",
            f"catalog-tiebreak-winner:{winner.key}",
        ),
    )


def _has_auto_numbering_evidence(parses: tuple[ParseResult, ...]) -> bool:
    return any(parse.absolute_episode is not None for parse in parses)


def _numbering_for_resolved_show(
    parses: tuple[ParseResult, ...],
    override: ShowOverride | None,
    provider: MetadataProvider,
    identity: ProviderIdentity,
) -> tuple[NumberingMode | None, tuple[str, ...], bool]:
    if override is not None:
        return override.numbering_mode, (), False
    if not _has_auto_numbering_evidence(parses):
        return NumberingMode.AIRED, (), False

    inference = infer_group_numbering_mode(
        parses,
        provider.episode_catalog(identity),
    )
    if not inference.attempted:
        return NumberingMode.AIRED, (), False
    return inference.mode, inference.reasons, True


def _resolved_show_result(
    *,
    source_key: str,
    parse_group: tuple[ParseResult, ...],
    override: ShowOverride | None,
    provider: MetadataProvider,
    provider_identity: ProviderIdentity,
    title: str,
    year: int | None,
    method: str,
    confidence: float,
    reasons: tuple[str, ...],
    candidates: tuple[CandidateEvidence, ...] = (),
) -> ShowResolution:
    mode, numbering_reasons, attempted = _numbering_for_resolved_show(
        parse_group,
        override,
        provider,
        provider_identity,
    )
    if mode is None:
        return ShowResolution(
            status=ResolutionStatus.SUSPICIOUS,
            show=None,
            evidence=MatchEvidence(
                method=f"{method}+numbering-inference" if attempted else method,
                confidence=confidence,
                reasons=(
                    "resolved-show-numbering-mode-not-unique",
                    *reasons,
                    *numbering_reasons,
                ),
                candidates=candidates,
            ),
        )

    return ShowResolution(
        status=ResolutionStatus.MATCHED,
        show=CanonicalShow(
            source_key=source_key,
            provider_identity=provider_identity,
            title=title,
            year=year,
            numbering_mode=mode,
        ),
        evidence=MatchEvidence(
            method=f"{method}+numbering-inference" if attempted else method,
            confidence=confidence,
            reasons=(*reasons, *numbering_reasons),
            candidates=candidates,
        ),
    )


def _candidate_gap(
    ranked: tuple[CandidateEvidence, ...],
) -> tuple[CandidateEvidence, float]:
    top = ranked[0]
    second_score = ranked[1].score if len(ranked) > 1 else 0.0
    return top, top.score - second_score


def _add_discovered_candidate(
    candidates: dict[ProviderIdentity, ProviderShow],
    candidate: ProviderShow,
) -> bool:
    previous = candidates.get(candidate.identity)
    if previous is not None and previous != candidate:
        return False
    candidates[candidate.identity] = candidate
    return True


def resolve_show_group_with_provider(
    source_key: str,
    parses: Iterable[ParseResult],
    overrides: OverrideCatalog,
    provider: MetadataProvider,
) -> ShowResolution:
    """Resolve one source-show group through normalized provider metadata."""

    parse_group = tuple(parses)
    titles = _source_titles(parse_group)
    source_title = _representative_title(titles)
    year_hint, year_consistent = _consistent_year(parse_group)
    if not year_consistent:
        return _unresolved("group-validation", "conflicting-source-years")

    override_matches = _matching_overrides(source_key, titles, overrides)
    if len(override_matches) > 1:
        return _unresolved("override", "ambiguous-override-match")
    override = override_matches[0] if override_matches else None

    if override is not None and override.year is not None:
        if year_hint is not None and override.year != year_hint:
            return _unresolved("override", "override-year-conflicts-with-source")
        year_hint = override.year

    explicit_identities = _explicit_identities(parse_group, override)
    if len(explicit_identities) > 1:
        return _unresolved(
            "explicit-provider-id",
            *_explicit_conflict_reasons(explicit_identities),
        )
    if explicit_identities:
        identity = explicit_identities[0]
        active_provider = ProviderIdentity.normalize_provider(provider.provider_name)
        if identity.provider != active_provider:
            return _unresolved(
                "explicit-provider-id",
                "explicit-provider-does-not-match-active-provider",
                f"provider-identity:{identity.key}",
                f"active-provider:{active_provider}",
            )
        title = _preferred_title(override, source_title, None)
        if title is None:
            return _unresolved("explicit-provider-id", "missing-canonical-title")
        method = (
            "explicit-tvmaze-id"
            if identity.provider == "tvmaze"
            else "explicit-provider-id"
        )
        return _resolved_show_result(
            source_key=source_key,
            parse_group=parse_group,
            override=override,
            provider=provider,
            provider_identity=identity,
            title=title,
            year=year_hint,
            method=method,
            confidence=1.0,
            reasons=(
                "single-explicit-provider-identity",
                f"provider-identity:{identity.key}",
            ),
        )

    search_title = source_title
    if override is not None and override.preferred_title:
        search_title = override.preferred_title
    provider_method = f"{provider.provider_name}-search"
    if search_title is None:
        return _unresolved(provider_method, "missing-source-title")

    snapshot = provider.search_shows(search_title)
    if not snapshot.resolved:
        return _unresolved(
            provider_method,
            snapshot.unresolved_reason or "provider-search-unresolved",
        )

    search_reasons: list[str] = []
    provider_candidates = snapshot.shows
    if not provider_candidates:
        candidates_by_identity: dict[ProviderIdentity, ProviderShow] = {}
        backoff_titles = _search_backoff_titles(search_title)
        if backoff_titles:
            provider_method = f"{provider_method}+search-backoff"
            search_reasons.append("provider-search-backoff:attempted")
        for backoff_title in backoff_titles:
            backoff = provider.search_shows(backoff_title)
            search_reasons.extend(
                (
                    f"provider-search-backoff-query:{normalize_show_identity(backoff_title)}",
                    f"provider-search-backoff-request:{backoff.request_key}",
                )
            )
            if not backoff.resolved:
                return _unresolved(
                    provider_method,
                    *search_reasons,
                    "provider-search-backoff:indeterminate",
                    backoff.unresolved_reason or "provider-search-unresolved",
                )
            for candidate in backoff.shows:
                if not _add_discovered_candidate(candidates_by_identity, candidate):
                    return _unresolved(
                        provider_method,
                        *search_reasons,
                        "provider-search-backoff:conflicting-candidate-metadata",
                        f"provider-identity:{candidate.identity.key}",
                    )
        if backoff_titles:
            search_reasons.append("provider-search-backoff:complete")

        merge_titles = token_merge_queries(search_title)
        if merge_titles:
            provider_method = f"{provider_method}+token-merge"
            search_reasons.append("provider-search-token-merge:attempted")
        for merge_title in merge_titles:
            merged = provider.search_shows(merge_title)
            search_reasons.extend(
                (
                    f"provider-search-token-merge-query:{normalize_show_identity(merge_title)}",
                    f"provider-search-token-merge-request:{merged.request_key}",
                )
            )
            if not merged.resolved:
                return _unresolved(
                    provider_method,
                    *search_reasons,
                    "provider-search-token-merge:indeterminate",
                    merged.unresolved_reason or "provider-search-unresolved",
                )
            for candidate in merged.shows:
                if not _add_discovered_candidate(candidates_by_identity, candidate):
                    return _unresolved(
                        provider_method,
                        *search_reasons,
                        "provider-search-token-merge:conflicting-candidate-metadata",
                        f"provider-identity:{candidate.identity.key}",
                    )
        if merge_titles:
            search_reasons.append("provider-search-token-merge:complete")

        provider_candidates = tuple(
            sorted(
                candidates_by_identity.values(),
                key=lambda candidate: (
                    normalize_show_identity(candidate.title),
                    candidate.title,
                    candidate.identity.key,
                ),
            )
        )

    if not provider_candidates:
        return _unresolved(
            provider_method,
            *search_reasons,
            "no-valid-provider-candidates",
        )

    identities = {normalize_show_identity(title) for title in titles}
    identities.add(normalize_show_identity(source_key))
    if override is not None:
        identities.add(normalize_show_identity(override.key))
        identities.update(normalize_show_identity(alias) for alias in override.aliases)
        if override.preferred_title:
            identities.add(normalize_show_identity(override.preferred_title))
    identity_tuple = tuple(sorted(identity for identity in identities if identity))

    evidence_by_identity = {
        candidate.identity: _score_candidate(candidate, identity_tuple, year_hint)
        for candidate in provider_candidates
    }
    ranked = tuple(
        sorted(
            evidence_by_identity.values(),
            key=lambda candidate: (
                -candidate.score,
                normalize_show_identity(candidate.title),
                candidate.provider_identity.key,
            ),
        )
    )
    top, gap = _candidate_gap(ranked)

    if top.score >= _MATCH_THRESHOLD and (
        len(ranked) == 1 or gap >= _MINIMUM_MATCH_GAP
    ):
        provider_show = next(
            candidate
            for candidate in provider_candidates
            if candidate.identity == top.provider_identity
        )
        title = _preferred_title(override, source_title, provider_show.title)
        assert title is not None
        return _resolved_show_result(
            source_key=source_key,
            parse_group=parse_group,
            override=override,
            provider=provider,
            provider_identity=provider_show.identity,
            title=title,
            year=(provider_show.year if provider_show.year is not None else year_hint),
            method=provider_method,
            confidence=top.score,
            reasons=(*search_reasons, f"candidate-gap:{gap:.3f}"),
            candidates=ranked,
        )

    alias_result = None
    active_ranked = ranked
    method = provider_method
    if top.score < _MATCH_THRESHOLD:
        alias_result = enrich_provider_alias_evidence(
            provider,
            provider_candidates,
            ranked,
            identity_tuple,
            year_hint,
        )
        active_ranked = alias_result.ranked
        if alias_result.attempted:
            method = f"{provider_method}+provider-aliases"
        top, gap = _candidate_gap(active_ranked)

        if (
            alias_result.attempted
            and not alias_result.indeterminate
            and top.score >= _MATCH_THRESHOLD
            and (len(active_ranked) == 1 or gap >= _MINIMUM_MATCH_GAP)
        ):
            provider_show = next(
                candidate
                for candidate in provider_candidates
                if candidate.identity == top.provider_identity
            )
            title = _preferred_title(override, source_title, provider_show.title)
            assert title is not None
            return _resolved_show_result(
                source_key=source_key,
                parse_group=parse_group,
                override=override,
                provider=provider,
                provider_identity=provider_show.identity,
                title=title,
                year=(
                    provider_show.year if provider_show.year is not None else year_hint
                ),
                method=method,
                confidence=top.score,
                reasons=(
                    *search_reasons,
                    *alias_result.reasons,
                    f"candidate-gap:{gap:.3f}",
                ),
                candidates=active_ranked,
            )

    alias_indeterminate = alias_result is not None and alias_result.indeterminate
    tie_break = None
    title_tie_break = None
    aired_rescue = None
    coordinate_title_rescue = None
    rescue = None
    if not alias_indeterminate:
        mode = _numbering_mode(override)
        if mode is NumberingMode.AIRED:
            segment_title_rescue = segment_counted_title_rescue(
                provider,
                parse_group,
                active_ranked,
                minimum_gap=_MINIMUM_MATCH_GAP,
                suspicious_threshold=_SUSPICIOUS_THRESHOLD,
            )
            if segment_title_rescue is not None:
                if segment_title_rescue.winner is None:
                    return ShowResolution(
                        status=ResolutionStatus.SUSPICIOUS,
                        show=None,
                        evidence=MatchEvidence(
                            method=f"{method}+segment-counted-title-rescue",
                            confidence=top.score,
                            reasons=(
                                *search_reasons,
                                *(
                                    alias_result.reasons
                                    if alias_result is not None
                                    else ()
                                ),
                                *segment_title_rescue.reasons,
                                f"candidate-gap:{gap:.3f}",
                            ),
                            candidates=segment_title_rescue.candidates,
                        ),
                    )
                provider_show = next(
                    candidate
                    for candidate in provider_candidates
                    if candidate.identity == segment_title_rescue.winner
                )
                title = _preferred_title(override, source_title, provider_show.title)
                assert title is not None
                return _resolved_show_result(
                    source_key=source_key,
                    parse_group=parse_group,
                    override=override,
                    provider=provider,
                    provider_identity=provider_show.identity,
                    title=title,
                    year=(
                        provider_show.year
                        if provider_show.year is not None
                        else year_hint
                    ),
                    method=f"{method}+segment-counted-title-rescue",
                    confidence=top.score,
                    reasons=(
                        *search_reasons,
                        *(alias_result.reasons if alias_result is not None else ()),
                        *segment_title_rescue.reasons,
                        f"candidate-gap:{gap:.3f}",
                    ),
                    candidates=segment_title_rescue.candidates,
                )

        tie_break = _catalog_tie_break(parse_group, mode, provider, active_ranked)
        if tie_break is not None and tie_break.winner is not None:
            provider_show = next(
                candidate
                for candidate in provider_candidates
                if candidate.identity == tie_break.winner
            )
            title = _preferred_title(override, source_title, provider_show.title)
            assert title is not None
            return ShowResolution(
                status=ResolutionStatus.MATCHED,
                show=CanonicalShow(
                    source_key=source_key,
                    provider_identity=provider_show.identity,
                    title=title,
                    year=(
                        provider_show.year
                        if provider_show.year is not None
                        else year_hint
                    ),
                    numbering_mode=mode,
                ),
                evidence=MatchEvidence(
                    method=f"{method}+catalog-tiebreak",
                    confidence=top.score,
                    reasons=(
                        *search_reasons,
                        *(alias_result.reasons if alias_result is not None else ()),
                        *tie_break.reasons,
                        f"candidate-gap:{gap:.3f}",
                    ),
                    candidates=tie_break.candidates,
                ),
            )

        if (
            tie_break is not None
            and tie_break.winner is None
            and mode is NumberingMode.AIRED
        ):
            title_tie_break = catalog_title_tiebreak(
                provider,
                parse_group,
                tie_break.candidates,
                minimum_gap=_MINIMUM_MATCH_GAP,
                suspicious_threshold=_SUSPICIOUS_THRESHOLD,
            )
            if title_tie_break is not None and title_tie_break.winner is not None:
                provider_show = next(
                    candidate
                    for candidate in provider_candidates
                    if candidate.identity == title_tie_break.winner
                )
                title = _preferred_title(override, source_title, provider_show.title)
                assert title is not None
                return _resolved_show_result(
                    source_key=source_key,
                    parse_group=parse_group,
                    override=override,
                    provider=provider,
                    provider_identity=provider_show.identity,
                    title=title,
                    year=(
                        provider_show.year
                        if provider_show.year is not None
                        else year_hint
                    ),
                    method=f"{method}+catalog-title-tiebreak",
                    confidence=top.score,
                    reasons=(
                        *search_reasons,
                        *(alias_result.reasons if alias_result is not None else ()),
                        *tie_break.reasons,
                        *title_tie_break.reasons,
                        f"candidate-gap:{gap:.3f}",
                    ),
                    candidates=title_tie_break.candidates,
                )

        if mode is NumberingMode.AIRED:
            coordinate_title_rescue = catalog_coordinate_title_rescue(
                provider, parse_group, active_ranked
            )
            if (
                coordinate_title_rescue is not None
                and coordinate_title_rescue.winner is not None
            ):
                provider_show = next(
                    candidate
                    for candidate in provider_candidates
                    if candidate.identity == coordinate_title_rescue.winner
                )
                title = _preferred_title(override, source_title, provider_show.title)
                assert title is not None
                return _resolved_show_result(
                    source_key=source_key,
                    parse_group=parse_group,
                    override=override,
                    provider=provider,
                    provider_identity=provider_show.identity,
                    title=title,
                    year=(
                        provider_show.year
                        if provider_show.year is not None
                        else year_hint
                    ),
                    method=f"{method}+catalog-coordinate-title-rescue",
                    confidence=top.score,
                    reasons=(
                        *search_reasons,
                        *(alias_result.reasons if alias_result is not None else ()),
                        *(tie_break.reasons if tie_break is not None else ()),
                        *(
                            title_tie_break.reasons
                            if title_tie_break is not None
                            else ()
                        ),
                        *coordinate_title_rescue.reasons,
                        f"candidate-gap:{gap:.3f}",
                    ),
                    candidates=coordinate_title_rescue.candidates,
                )

            aired_rescue = aired_catalog_rescue(provider, parse_group, active_ranked)
            if aired_rescue is not None and aired_rescue.winner is not None:
                provider_show = next(
                    candidate
                    for candidate in provider_candidates
                    if candidate.identity == aired_rescue.winner
                )
                title = _preferred_title(override, source_title, provider_show.title)
                assert title is not None
                return _resolved_show_result(
                    source_key=source_key,
                    parse_group=parse_group,
                    override=override,
                    provider=provider,
                    provider_identity=provider_show.identity,
                    title=title,
                    year=(
                        provider_show.year
                        if provider_show.year is not None
                        else year_hint
                    ),
                    method=f"{method}+aired-catalog-rescue",
                    confidence=top.score,
                    reasons=(
                        *search_reasons,
                        *(alias_result.reasons if alias_result is not None else ()),
                        *(tie_break.reasons if tie_break is not None else ()),
                        *(
                            title_tie_break.reasons
                            if title_tie_break is not None
                            else ()
                        ),
                        *aired_rescue.reasons,
                        f"candidate-gap:{gap:.3f}",
                    ),
                    candidates=aired_rescue.candidates,
                )

        if tie_break is None and aired_rescue is None:
            rescue = catalog_group_rescue(provider, parse_group, active_ranked)
            if (
                rescue is not None
                and rescue.winner is not None
                and rescue.numbering_mode is not None
            ):
                provider_show = next(
                    candidate
                    for candidate in provider_candidates
                    if candidate.identity == rescue.winner
                )
                title = _preferred_title(override, source_title, provider_show.title)
                assert title is not None
                return ShowResolution(
                    status=ResolutionStatus.MATCHED,
                    show=CanonicalShow(
                        source_key=source_key,
                        provider_identity=provider_show.identity,
                        title=title,
                        year=(
                            provider_show.year
                            if provider_show.year is not None
                            else year_hint
                        ),
                        numbering_mode=rescue.numbering_mode,
                    ),
                    evidence=MatchEvidence(
                        method=f"{method}+catalog-rescue",
                        confidence=top.score,
                        reasons=(
                            *search_reasons,
                            *(alias_result.reasons if alias_result is not None else ()),
                            *rescue.reasons,
                            f"candidate-gap:{gap:.3f}",
                        ),
                        candidates=rescue.candidates,
                    ),
                )

    status = (
        ResolutionStatus.SUSPICIOUS
        if top.score >= _SUSPICIOUS_THRESHOLD
        else ResolutionStatus.UNRESOLVED
    )
    reason = (
        "ambiguous-top-candidates"
        if status is ResolutionStatus.SUSPICIOUS
        else "provider-evidence-below-threshold"
    )
    candidates = active_ranked
    if title_tie_break is not None:
        candidates = title_tie_break.candidates
    elif coordinate_title_rescue is not None:
        candidates = coordinate_title_rescue.candidates
    elif aired_rescue is not None:
        candidates = aired_rescue.candidates
    elif tie_break is not None:
        candidates = tie_break.candidates
    elif rescue is not None:
        candidates = rescue.candidates
    return ShowResolution(
        status=status,
        show=None,
        evidence=MatchEvidence(
            method=method,
            confidence=top.score,
            reasons=(
                reason,
                *search_reasons,
                *(alias_result.reasons if alias_result is not None else ()),
                *(tie_break.reasons if tie_break is not None else ()),
                *(title_tie_break.reasons if title_tie_break is not None else ()),
                *(
                    coordinate_title_rescue.reasons
                    if coordinate_title_rescue is not None
                    else ()
                ),
                *(aired_rescue.reasons if aired_rescue is not None else ()),
                *(rescue.reasons if rescue is not None else ()),
                f"candidate-gap:{gap:.3f}",
            ),
            candidates=candidates,
        ),
    )


def resolve_show_group(
    source_key: str,
    parses: Iterable[ParseResult],
    overrides: OverrideCatalog,
    cache: TvmazeCatalogCache,
    getter: JsonGetter,
) -> ShowResolution:
    """Compatibility wrapper using the TVMaze provider plus cached alias evidence."""

    return resolve_show_group_with_provider(
        source_key,
        parses,
        overrides,
        TvmazeAliasProviderAdapter(cache, getter),
    )
