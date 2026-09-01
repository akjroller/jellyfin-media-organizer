from __future__ import annotations

import pytest

from jellyfin_show_organizer.duplicate_classifier import (
    DuplicateCandidate,
    classify_duplicate_candidates,
)
from jellyfin_show_organizer.models import SourceFingerprint

pytestmark = pytest.mark.local

_DESTINATION = "Example Series/Season 01/Example Series S01E01.mkv"
_IDENTITY = "fixture:show:episode-one"


def _candidate(source: str) -> DuplicateCandidate:
    return DuplicateCandidate(
        operation_key=source,
        members=(source,),
        destination=_DESTINATION,
        logical_identity=_IDENTITY,
        fingerprint=SourceFingerprint(size=1_000, mtime_ns=1),
    )


def _decision(*sources: str):
    candidates = tuple(_candidate(source) for source in sources)
    (result,) = classify_duplicate_candidates(candidates)
    return result.decision


def test_uniform_unknown_source_can_use_unique_resolution_dominance() -> None:
    lower = "Example.Series.S01E01.720p.x265-GROUP.mkv"
    higher = "Example.Series.S01E01.1080p.x265-GROUP.mkv"

    decision = _decision(lower, higher)

    assert decision.winner == higher
    assert decision.losers == (lower,)
    assert any(
        "all release source families are unknown" in item for item in decision.evidence
    )


def test_known_source_can_beat_strictly_lower_resolution_unknown_source() -> None:
    lower = "Example.Series.S01E01.720p.x265-GROUP.mkv"
    higher = "Example.Series.S01E01.1080p.BluRay.x265-GROUP.mkv"

    decision = _decision(lower, higher)

    assert decision.winner == higher
    assert decision.losers == (lower,)
    assert any(
        "known-source winner exceeds lower-resolution unknown-source candidates" in item
        for item in decision.evidence
    )


def test_unknown_source_cannot_win_over_known_source() -> None:
    unknown_higher = "Example.Series.S01E01.1080p.x265-GROUP.mkv"
    known_lower = "Example.Series.S01E01.720p.WEB-DL.x265-GROUP.mkv"

    decision = _decision(unknown_higher, known_lower)

    assert decision.winner is None
    assert decision.losers == ()
    assert "release-quality-policy:release-quality evidence is incomplete" in (
        decision.evidence
    )


def test_unknown_source_tie_with_known_source_remains_unresolved() -> None:
    unknown = "Example.Series.S01E01.1080p.x265-GROUP-A.mkv"
    known = "Example.Series.S01E01.1080p.WEB-DL.x265-GROUP-B.mkv"

    decision = _decision(unknown, known)

    assert decision.winner is None
    assert decision.losers == ()
