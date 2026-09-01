from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from .models import CandidateEvidence, NumberingMode, ParseResult, ProviderIdentity
from .numbering_inference import infer_group_numbering_mode
from .provider_aliases import ProviderAliasSnapshot
from .providers import MetadataProvider, ProviderShow

_ALIAS_SIMILARITY_FLOOR = 0.90
_ALIAS_SIMILARITY_WEIGHT = 0.86


@dataclass(frozen=True, slots=True)
class AliasEnrichment:
    ranked: tuple[CandidateEvidence, ...]
    attempted: bool
    indeterminate: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogGroupRescue:
    winner: ProviderIdentity | None
    numbering_mode: NumberingMode | None
    candidates: tuple[CandidateEvidence, ...]
    reasons: tuple[str, ...] = ()


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _alias_score(
    show: ProviderShow,
    snapshot: ProviderAliasSnapshot,
    identities: tuple[str, ...],
    year_hint: int | None,
) -> tuple[float | None, tuple[str, ...]]:
    normalized_aliases = tuple(
        sorted(
            {
                normalized
                for alias in snapshot.aliases
                if (normalized := _normalize(alias))
            }
        )
    )
    if not normalized_aliases:
        return None, ()

    exact = tuple(alias for alias in normalized_aliases if alias in identities)
    if exact:
        score = 0.90
        reasons: list[str] = [f"exact-normalized-provider-alias:{exact[0]}"]
    else:
        ratios = tuple(
            SequenceMatcher(None, identity, alias).ratio()
            for identity in identities
            for alias in normalized_aliases
            if identity
        )
        best_ratio = max(ratios, default=0.0)
        if best_ratio < _ALIAS_SIMILARITY_FLOOR:
            return None, (f"provider-alias-similarity:{best_ratio:.3f}",)
        score = _ALIAS_SIMILARITY_WEIGHT * best_ratio
        reasons = [f"provider-alias-similarity:{best_ratio:.3f}"]

    if year_hint is not None:
        if show.year == year_hint:
            score = min(1.0, score + 0.10)
            reasons.append("provider-alias-year-match")
        elif show.year is None:
            score = min(score, 0.79)
            reasons.append("provider-alias-candidate-year-missing")
        else:
            score = min(score * 0.45, 0.45)
            reasons.append("provider-alias-year-mismatch")

    return round(score, 6), tuple(reasons)


def enrich_provider_alias_evidence(
    provider: MetadataProvider,
    shows: tuple[ProviderShow, ...],
    ranked: tuple[CandidateEvidence, ...],
    identities: tuple[str, ...],
    year_hint: int | None,
) -> AliasEnrichment:
    """Add lazy provider alias evidence without weakening the match threshold."""

    loader = getattr(provider, "show_aliases", None)
    if not callable(loader):
        return AliasEnrichment(
            ranked=ranked,
            attempted=False,
            indeterminate=False,
        )

    evidence_by_identity = {
        candidate.provider_identity: candidate for candidate in ranked
    }
    indeterminate = False
    reasons: list[str] = ["provider-alias-evidence:attempted"]

    for show in sorted(shows, key=lambda candidate: candidate.identity.key):
        snapshot = loader(show.identity)
        candidate = evidence_by_identity[show.identity]
        common_reasons = (
            f"provider-alias-request:{snapshot.request_key}",
            f"provider-alias-snapshot:{snapshot.cache_snapshot_id}",
        )
        if not snapshot.resolved:
            indeterminate = True
            evidence_by_identity[show.identity] = replace(
                candidate,
                reasons=(
                    *candidate.reasons,
                    *common_reasons,
                    "provider-alias-unresolved:"
                    f"{snapshot.unresolved_reason or 'provider-aliases-unresolved'}",
                ),
            )
            continue
        if snapshot.errors:
            indeterminate = True
            evidence_by_identity[show.identity] = replace(
                candidate,
                reasons=(
                    *candidate.reasons,
                    *common_reasons,
                    *(f"provider-alias-error:{error}" for error in snapshot.errors),
                ),
            )
            continue

        alias_score, alias_reasons = _alias_score(
            show,
            snapshot,
            identities,
            year_hint,
        )
        evidence_by_identity[show.identity] = replace(
            candidate,
            score=(
                max(candidate.score, alias_score)
                if alias_score is not None
                else candidate.score
            ),
            reasons=(
                *candidate.reasons,
                *common_reasons,
                *alias_reasons,
            ),
        )

    enriched = tuple(
        sorted(
            evidence_by_identity.values(),
            key=lambda candidate: (
                -candidate.score,
                _normalize(candidate.title),
                candidate.provider_identity.key,
            ),
        )
    )
    if indeterminate:
        reasons.append("provider-alias-evidence:indeterminate")
    else:
        reasons.append("provider-alias-evidence:complete")
    return AliasEnrichment(
        ranked=enriched,
        attempted=True,
        indeterminate=indeterminate,
        reasons=tuple(reasons),
    )


def _catalog_rescue_mode(parses: tuple[ParseResult, ...]) -> NumberingMode | None:
    """Return one already-unambiguous numbering family suitable for show rescue.

    Low-confidence show identity rescue must not use the provider catalog to decide
    two things at once. Dual aired/absolute evidence belongs to the #109 numbering
    inference path after the show is already resolved; here every source must carry
    one complete, consistent aired or absolute family before any catalog is fetched.
    """

    if not parses:
        return None

    modes: list[NumberingMode] = []
    for parse in parses:
        has_aired = parse.season is not None or bool(parse.episodes)
        has_absolute = parse.absolute_episode is not None
        has_other = any(
            (
                parse.special_episode is not None,
                parse.episode_date is not None,
                parse.segment_hint is not None,
            )
        )
        if sum((has_aired, has_absolute, has_other)) != 1:
            return None

        if has_aired:
            if parse.season is None or not parse.episodes:
                return None
            modes.append(NumberingMode.AIRED)
            continue
        if has_absolute:
            if parse.absolute_episode is None or parse.absolute_episode <= 0:
                return None
            modes.append(NumberingMode.ABSOLUTE)
            continue
        return None

    first = modes[0]
    if any(mode is not first for mode in modes[1:]):
        return None
    return first


def catalog_group_rescue(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
) -> CatalogGroupRescue | None:
    """Rescue non-decisive text only when one full candidate catalog wins.

    Every provider-search candidate is evaluated so a low textual score cannot hide
    a catalog-compatible competitor. Any incomplete candidate catalog blocks the
    rescue. A candidate itself is compatible only when the group's already-
    unambiguous numbering family is uniquely compatible with that candidate's
    catalog.
    """

    expected_mode = _catalog_rescue_mode(parses)
    if not ranked or expected_mode is None:
        return None

    outcomes: dict[ProviderIdentity, NumberingMode | None] = {}
    candidate_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    indeterminate = False

    for candidate in sorted(ranked, key=lambda item: item.provider_identity.key):
        catalog = provider.episode_catalog(candidate.provider_identity)
        inference = infer_group_numbering_mode(parses, catalog)
        candidate_reasons[candidate.provider_identity] = (
            f"catalog-rescue-request:{catalog.request_key}",
            *inference.reasons,
        )
        if "numbering-inference:indeterminate-catalog" in inference.reasons:
            indeterminate = True
        outcomes[candidate.provider_identity] = (
            inference.mode if inference.mode is expected_mode else None
        )

    enriched = tuple(
        replace(
            candidate,
            reasons=(
                *candidate.reasons,
                *candidate_reasons.get(candidate.provider_identity, ()),
            ),
        )
        for candidate in ranked
    )
    if indeterminate:
        return CatalogGroupRescue(
            winner=None,
            numbering_mode=None,
            candidates=enriched,
            reasons=("catalog-rescue:indeterminate-candidate-catalog",),
        )

    winners = tuple(
        (identity, mode)
        for identity, mode in sorted(outcomes.items(), key=lambda item: item[0].key)
        if mode is not None
    )
    if len(winners) != 1:
        return CatalogGroupRescue(
            winner=None,
            numbering_mode=None,
            candidates=enriched,
            reasons=("catalog-rescue:no-unique-compatible-candidate",),
        )

    winner, mode = winners[0]
    assert mode is not None
    winner_first = tuple(
        sorted(
            enriched,
            key=lambda candidate: (
                candidate.provider_identity != winner,
                -candidate.score,
                _normalize(candidate.title),
                candidate.provider_identity.key,
            ),
        )
    )
    return CatalogGroupRescue(
        winner=winner,
        numbering_mode=mode,
        candidates=winner_first,
        reasons=(
            "catalog-rescue:unique-compatible-candidate",
            f"catalog-rescue-winner:{winner.key}",
            f"catalog-rescue-numbering-mode:{mode.value}",
        ),
    )
