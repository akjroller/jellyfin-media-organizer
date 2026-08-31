from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .models import DuplicateDecision, SourceFingerprint


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
) -> tuple[DuplicateCandidate | None, tuple[str, ...]]:
    if any(candidate.preference is None for candidate in candidates):
        return None, ()

    ranked = sorted(
        candidates,
        key=lambda candidate: (
            candidate.preference.rank if candidate.preference is not None else -1,
            candidate.operation_key.casefold(),
            candidate.operation_key,
        ),
        reverse=True,
    )
    winner = ranked[0]
    runner_up = ranked[1]
    assert winner.preference is not None
    assert runner_up.preference is not None
    if winner.preference.rank == runner_up.preference.rank:
        return None, ()

    return winner, (
        f"explicit preference rank {winner.preference.rank} selected {winner.operation_key}",
        *winner.preference.reasons,
    )


def _duplicate_result(
    destination_key: str,
    candidates: tuple[DuplicateCandidate, ...],
) -> DuplicateGroupResult:
    winner, evidence = _exact_hash_winner(candidates)
    confidence = 1.0

    if winner is None:
        winner, evidence = _preference_winner(candidates)
        confidence = 0.9

    if winner is None:
        decision = DuplicateDecision(
            destination_key=destination_key,
            candidates=_decision_candidates(candidates),
            winner=None,
            losers=(),
            confidence=0.5,
            evidence=(
                "candidates share one logical identity but no deterministic winner evidence exists",
            ),
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
        evidence=evidence,
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
    identities = sorted({_normalize_key(candidate.logical_identity) for candidate in candidates})
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

    Winner selection is fail-closed. Exact SHA-256 equality may select a stable
    representative, and an explicit preference rank may select a unique winner.
    Size, timestamps, path length, or input order are never used as quality
    evidence.
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
