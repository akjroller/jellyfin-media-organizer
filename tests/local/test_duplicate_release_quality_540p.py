from __future__ import annotations

import pytest

from jellyfin_show_organizer.duplicate_classifier import (
    DuplicateCandidate,
    classify_duplicate_candidates,
)
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.release_quality import parse_release_quality

pytestmark = pytest.mark.local

_DESTINATION = "Example Series/Season 01/Example Series S01E01.mkv"
_IDENTITY = "fixture:show:episode-one"


def _candidate(source: str) -> DuplicateCandidate:
    return DuplicateCandidate(
        operation_key=source,
        members=(source,),
        destination=_DESTINATION,
        logical_identity=_IDENTITY,
        fingerprint=SourceFingerprint(size=1_000, mtime_ns=1, sha256=None),
    )


def test_release_quality_parser_recognizes_540p() -> None:
    evidence = parse_release_quality("Example.Series.S01E01.540p.WEB-DL.mkv")

    assert evidence.resolution == 540
    assert evidence.errors == ()


def test_720p_uniquely_beats_540p_with_same_source_family() -> None:
    lower = _candidate("Example.Series.S01E01.540p.WEB-DL.mkv")
    higher = _candidate("Example.Series.S01E01.720p.WEB-DL.mkv")

    (result,) = classify_duplicate_candidates((lower, higher))
    decision = result.decision

    assert decision.winner == higher.operation_key
    assert decision.losers == (lower.operation_key,)
    assert decision.confidence == 0.8
    assert (
        "release-quality-policy:unique release-quality dominance via resolution"
        in decision.evidence
    )
    assert decision.evidence[-1] == (
        "non-selected candidates are duplicate/non-moving only; no deletion is authorized"
    )
