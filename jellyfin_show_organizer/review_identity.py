from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass


def normalize_review_path(value: str) -> str:
    return unicodedata.normalize("NFKC", value.replace("\\", "/")).casefold()


@dataclass(frozen=True, slots=True)
class ReviewFingerprint:
    size: int
    mtime_ns: int
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.size < 0 or self.mtime_ns < 0:
            raise ValueError("review fingerprint values cannot be negative")
        if self.sha256 is not None:
            digest = self.sha256.casefold()
            if len(digest) != 64:
                raise ValueError("review fingerprint sha256 must contain 64 hex characters")
            try:
                int(digest, 16)
            except ValueError as exc:
                raise ValueError(
                    "review fingerprint sha256 must contain 64 hex characters"
                ) from exc
            object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True, slots=True)
class ReviewMemberBinding:
    path: str
    fingerprint: ReviewFingerprint

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("review member path cannot be empty")


@dataclass(frozen=True, slots=True)
class ReviewCandidateBinding:
    source: ReviewMemberBinding
    companions: tuple[ReviewMemberBinding, ...] = ()

    def __post_init__(self) -> None:
        keys = [
            normalize_review_path(member.path)
            for member in (self.source, *self.companions)
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("review candidate members must be unique")


def _canonical_candidate(candidate: ReviewCandidateBinding) -> dict[str, object]:
    members = (candidate.source, *candidate.companions)
    return {
        "source": normalize_review_path(candidate.source.path),
        "members": sorted(
            (
                {
                    "path": normalize_review_path(member.path),
                    "size": member.fingerprint.size,
                    "mtime_ns": member.fingerprint.mtime_ns,
                    "sha256": member.fingerprint.sha256,
                }
                for member in members
            ),
            key=lambda item: str(item["path"]),
        ),
    }


def source_binding_hash(candidate: ReviewCandidateBinding) -> str:
    """Bind one reviewed video to its fingerprint and companion member set."""

    encoded = json.dumps(
        _canonical_candidate(candidate),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_duplicate_ref(destination_key: str, candidates: Iterable[str]) -> str:
    identity = {
        "destination_key": normalize_review_path(destination_key),
        "candidates": sorted(normalize_review_path(value) for value in candidates),
    }
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"duplicate-{hashlib.sha256(payload).hexdigest()[:16]}"


def duplicate_candidate_set_hash(
    destination_key: str,
    candidates: Iterable[ReviewCandidateBinding],
) -> str:
    canonical = [_canonical_candidate(candidate) for candidate in candidates]
    canonical.sort(key=lambda item: str(item["source"]))
    payload = {
        "destination_key": normalize_review_path(destination_key),
        "candidates": canonical,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
