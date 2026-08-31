from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
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
from .overrides import OverrideCatalog, ShowOverride
from .providers import MetadataProvider, ProviderShow, TvmazeProviderAdapter
from .tvmaze_cache import JsonGetter, TvmazeCatalogCache

_MATCH_THRESHOLD = 0.90
_SUSPICIOUS_THRESHOLD = 0.75
_MINIMUM_MATCH_GAP = 0.08


class ResolutionStatus(StrEnum):
    MATCHED = "matched"
    SUSPICIOUS = "suspicious"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ShowResolution:
    status: ResolutionStatus
    show: CanonicalShow | None
    evidence: MatchEvidence


def normalize_show_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


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
        score = 0.72 * best_ratio
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
        title = _preferred_title(override, source_title, None)
        if title is None:
            return _unresolved("explicit-provider-id", "missing-canonical-title")
        method = (
            "explicit-tvmaze-id"
            if identity.provider == "tvmaze"
            else "explicit-provider-id"
        )
        return ShowResolution(
            status=ResolutionStatus.MATCHED,
            show=CanonicalShow(
                source_key=source_key,
                provider_identity=identity,
                title=title,
                year=year_hint,
                numbering_mode=_numbering_mode(override),
            ),
            evidence=MatchEvidence(
                method=method,
                confidence=1.0,
                reasons=(
                    "single-explicit-provider-identity",
                    f"provider-identity:{identity.key}",
                ),
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
            f"provider-snapshot:{snapshot.snapshot_identity}",
        )

    provider_candidates = snapshot.shows
    if not provider_candidates:
        return _unresolved(
            provider_method,
            "no-valid-provider-candidates",
            f"provider-snapshot:{snapshot.snapshot_identity}",
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
    top = ranked[0]
    second_score = ranked[1].score if len(ranked) > 1 else 0.0
    gap = top.score - second_score

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
        return ShowResolution(
            status=ResolutionStatus.MATCHED,
            show=CanonicalShow(
                source_key=source_key,
                provider_identity=provider_show.identity,
                title=title,
                year=(
                    provider_show.year if provider_show.year is not None else year_hint
                ),
                numbering_mode=_numbering_mode(override),
            ),
            evidence=MatchEvidence(
                method=provider_method,
                confidence=top.score,
                reasons=(
                    f"candidate-gap:{gap:.3f}",
                    f"provider-snapshot:{snapshot.snapshot_identity}",
                ),
                candidates=ranked,
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
    return ShowResolution(
        status=status,
        show=None,
        evidence=MatchEvidence(
            method=provider_method,
            confidence=top.score,
            reasons=(
                reason,
                f"candidate-gap:{gap:.3f}",
                f"provider-snapshot:{snapshot.snapshot_identity}",
            ),
            candidates=ranked,
        ),
    )


def resolve_show_group(
    source_key: str,
    parses: Iterable[ParseResult],
    overrides: OverrideCatalog,
    cache: TvmazeCatalogCache,
    getter: JsonGetter,
) -> ShowResolution:
    """Compatibility wrapper using the initial TVMaze provider adapter."""

    return resolve_show_group_with_provider(
        source_key,
        parses,
        overrides,
        TvmazeProviderAdapter(cache, getter),
    )
