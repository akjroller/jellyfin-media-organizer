from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .models import DuplicateDecision, SourceFingerprint
from .release_quality import (
    ReleaseQualityEvidence,
    parse_release_quality,
    select_unique_release_quality_winner,
)


class DuplicateDisposition(StrEnum):
    DUPLICATE = "duplicate"
    SUSPICIOUS = "suspicious"


@dataclass(frozen=True, slots=True)
class DuplicatePreference:
    """Explicit caller-supplied preference evidence for one source operation group."""

    rank: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.rank < 0:
            raise ValueError("duplicate preference rank cannot be negative")
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("duplicate preference requires non-empty reasons")


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    """One indivisible source operation group competing for a destination."""

    operation_key: str
    members: tuple[str, ...]
    destination: str
    logical_identity: str
    fingerprint: SourceFingerprint
    preference: DuplicatePreference | None = None
    release_quality: ReleaseQualityEvidence | None = None

    def __post_init__(self) -> None:
        if not self.operation_key.strip():
            raise ValueError("duplicate candidate operation_key cannot be empty")
        if not self.members or any(not member.strip() for member in self.members):
            raise ValueError("duplicate candidate requires non-empty members")
        if not self.destination.strip():
            raise ValueError("duplicate candidate destination cannot be empty")
        if not self.logical_identity.strip():
            raise ValueError("duplicate candidate logical_identity cannot be empty")

        member_keys = [_normalize_key(member) for member in self.members]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("duplicate candidate members must be unique")
        if self.release_quality is None:
            object.__setattr__(
                self,
                "release_quality",
                parse_release_quality(self.operation_key),
            )


@dataclass(frozen=True, slots=True)
class DuplicateGroupResult:
    disposition: DuplicateDisposition
    decision: DuplicateDecision
    candidates: tuple[DuplicateCandidate, ...]


def _normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.replace("\\", "/"))
    return normalized.casefold()


def _candidate_sort_key(candidate: DuplicateCandidate) -> tuple[str, str]:
    return candidate.operation_key.casefold(), candidate.operation_key


def _decision_candidates(
    candidates: tuple[DuplicateCandidate, ...],
) -> tuple[str, ...]:
    return tuple(candidate.operation_key for candidate in candidates)


def _quality_audit_evidence(
    candidates: tuple[DuplicateCandidate, ...],
) -> tuple[str, ...]:
    evidence: list[str] = []
    for candidate in candidates:
        quality = candidate.release_quality
        assert quality is not None
        evidence.append(f"release-quality:{candidate.operation_key}:{quality.summary}")
    return tuple(evidence)


def _exact_hash_winner(
    candidates: tuple[DuplicateCandidate, ...],
) -> tuple[DuplicateCandidate | None, tuple[str, ...]]:
    hashes = tuple(candidate.fingerprint.sha256 for candidate in candidates)
    if any(digest is None for digest in hashes) or len(set(hashes)) != 1:
        return None, ()

    winner = candidates[0]
    return winner, (
        "all candidates share the same exact SHA-256 digest",
        "operation-key ordering is used only as a deterministic equivalent-file tiebreaker",
    )


def _preference_winner(
    candidates: tuple[DuplicateCandidate, ...],
) -> tuple[DuplicateCandidate | None, tuple[str, ...], bool]:
    preferred = tuple(
        candidate for candidate in candidates if candidate.preference is not None
    )
    if not preferred:
        return None, (), False

    ranked = sorted(
        preferred,
        key=lambda candidate: (
            candidate.preference.rank if candidate.preference is not None else -1,
            candidate.operation_key.casefold(),
            candidate.operation_key,
        ),
        reverse=True,
    )
    winner = ranked[0]
    assert winner.preference is not None
    if len(ranked) > 1:
        runner_up = ranked[1]
        assert runner_up.preference is not None
        if winner.preference.rank == runner_up.preference.rank:
            return (
                None,
                (
                    "explicit duplicate preferences do not produce a unique highest rank",
                    "automatic release-quality evidence is not allowed to override tied explicit preferences",
                ),
                True,
            )

    return (
        winner,
        (
            f"explicit preference rank {winner.preference.rank} selected {winner.operation_key}",
            *winner.preference.reasons,
        ),
        True,
    )


def _release_quality_winner(
    candidates: tuple[DuplicateCandidate, ...],
) -> tuple[DuplicateCandidate | None, tuple[str, ...]]:
    quality: list[ReleaseQualityEvidence] = []
    for candidate in candidates:
        item = candidate.release_quality
        assert item is not None
        quality.append(item)

    winner_index, reason = select_unique_release_quality_winner(tuple(quality))
    audit = _quality_audit_evidence(candidates)
    if winner_index is None:
        return None, (*audit, f"release-quality-policy:{reason}")

    winner = candidates[winner_index]
    return winner, (
        *audit,
        f"release-quality-policy:{reason}",
        f"release-quality-winner:{winner.operation_key}",
    )


def _with_safety_evidence(
    evidence: tuple[str, ...],
    candidates: tuple[DuplicateCandidate, ...],
) -> tuple[str, ...]:
    existing_quality = any(item.startswith("release-quality:") for item in evidence)
    quality = () if existing_quality else _quality_audit_evidence(candidates)
    return (
        *evidence,
        *quality,
        "non-selected candidates are duplicate/non-moving only; no deletion is authorized",
    )


def _duplicate_result(
    destination_key: str,
    candidates: tuple[DuplicateCandidate, ...],
) -> DuplicateGroupResult:
    winner, evidence = _exact_hash_winner(candidates)
    confidence = 1.0

    preference_present = False
    if winner is None:
        winner, evidence, preference_present = _preference_winner(candidates)
        confidence = 0.9

    if winner is None and not preference_present:
        winner, evidence = _release_quality_winner(candidates)
        confidence = 0.8

    if winner is None:
        if not evidence:
            evidence = (
                "candidates share one logical identity but no deterministic winner evidence exists",
            )
        decision = DuplicateDecision(
            destination_key=destination_key,
            candidates=_decision_candidates(candidates),
            winner=None,
            losers=(),
            confidence=0.5,
            evidence=_with_safety_evidence(evidence, candidates),
        )
        return DuplicateGroupResult(
            disposition=DuplicateDisposition.DUPLICATE,
            decision=decision,
            candidates=candidates,
        )

    losers = tuple(
        candidate.operation_key for candidate in candidates if candidate is not winner
    )
    decision = DuplicateDecision(
        destination_key=destination_key,
        candidates=_decision_candidates(candidates),
        winner=winner.operation_key,
        losers=losers,
        confidence=confidence,
        evidence=_with_safety_evidence(evidence, candidates),
    )
    return DuplicateGroupResult(
        disposition=DuplicateDisposition.DUPLICATE,
        decision=decision,
        candidates=candidates,
    )


def _suspicious_result(
    destination_key: str,
    candidates: tuple[DuplicateCandidate, ...],
) -> DuplicateGroupResult:
    identities = sorted(
        {_normalize_key(candidate.logical_identity) for candidate in candidates}
    )
    decision = DuplicateDecision(
        destination_key=destination_key,
        candidates=_decision_candidates(candidates),
        winner=None,
        losers=(),
        confidence=0.0,
        evidence=(
            "destination convergence spans multiple logical identities",
            f"logical identity count: {len(identities)}",
        ),
    )
    return DuplicateGroupResult(
        disposition=DuplicateDisposition.SUSPICIOUS,
        decision=decision,
        candidates=candidates,
    )


def classify_duplicate_candidates(
    candidates: Iterable[DuplicateCandidate],
) -> tuple[DuplicateGroupResult, ...]:
    """Classify destination collisions without performing any media mutation.

    Candidates are grouped by a Unicode-normalized, case-insensitive destination
    key. A group is a true duplicate candidate only when every source operation
    refers to the same logical identity. Different logical identities converging
    on one destination are suspicious and never receive a winner.

    Winner selection is fail-closed. Exact SHA-256 equality has highest authority,
    followed by a unique highest explicit preference. Automatic release-quality
    evidence is considered only after those cases and only when one candidate
    uniquely dominates all others within compatible source/remux dimensions.
    Size, timestamps, path length, lexical order, or input order are never quality
    evidence. No duplicate decision authorizes deletion.
    """

    grouped: dict[str, list[DuplicateCandidate]] = defaultdict(list)
    seen_operations: set[str] = set()

    for candidate in candidates:
        operation_key = _normalize_key(candidate.operation_key)
        if operation_key in seen_operations:
            raise ValueError(
                f"duplicate operation_key supplied more than once: {candidate.operation_key}"
            )
        seen_operations.add(operation_key)
        grouped[_normalize_key(candidate.destination)].append(candidate)

    results: list[DuplicateGroupResult] = []
    for destination_key in sorted(grouped):
        group = tuple(sorted(grouped[destination_key], key=_candidate_sort_key))
        if len(group) < 2:
            continue

        identities = {_normalize_key(candidate.logical_identity) for candidate in group}
        if len(identities) == 1:
            results.append(_duplicate_result(destination_key, group))
        else:
            results.append(_suspicious_result(destination_key, group))

    return tuple(results)
