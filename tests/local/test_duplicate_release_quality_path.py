from __future__ import annotations

import pytest

from jellyfin_show_organizer.duplicate_classifier import (
    DuplicateCandidate,
    classify_duplicate_candidates,
)
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.release_quality import (
    ReleaseSourceFamily,
    parse_release_quality,
)

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


def test_parent_release_directory_supplies_quality_evidence() -> None:
    evidence = parse_release_quality(
        "Example.Series.S01.1080p.WEB-Rip/Example.Series.S01E01.mkv"
    )

    assert evidence.resolution == 1080
    assert evidence.source_family is ReleaseSourceFamily.WEB_RIP
    assert evidence.errors == ()


def test_parent_and_filename_can_supply_different_supported_dimensions() -> None:
    evidence = parse_release_quality(
        "Example.Series.S01.1080p/Example.Series.S01E01.WEB-DL.REPACK.mkv"
    )

    assert evidence.resolution == 1080
    assert evidence.source_family is ReleaseSourceFamily.WEB_DL
    assert evidence.revision_rank == 2
    assert evidence.revision_markers == ("repack",)
    assert evidence.errors == ()


def test_conflicting_parent_and_filename_resolutions_fail_closed() -> None:
    evidence = parse_release_quality(
        "Example.Series.S01.1080p.WEB-DL/Example.Series.S01E01.720p.WEB-DL.mkv"
    )

    assert evidence.resolution is None
    assert evidence.errors == ("multiple-resolutions:720,1080",)


def test_conflicting_parent_and_filename_sources_fail_closed() -> None:
    evidence = parse_release_quality(
        "Example.Series.S01.1080p.WEB-DL/Example.Series.S01E01.1080p.BluRay.mkv"
    )

    assert evidence.source_family is None
    assert evidence.errors == ("multiple-source-families:blu-ray,web-dl",)


def test_generic_ancestor_folders_do_not_change_supported_evidence() -> None:
    evidence = parse_release_quality(
        "Example Series/Season 01/Release.1080p.WEB-DL/Example.Series.S01E01.mkv"
    )

    assert evidence.resolution == 1080
    assert evidence.source_family is ReleaseSourceFamily.WEB_DL
    assert evidence.errors == ()


def test_filename_only_quality_parsing_remains_unchanged() -> None:
    evidence = parse_release_quality("Example.Series.S01E01.720p.HDTV.PROPER.mkv")

    assert evidence.resolution == 720
    assert evidence.source_family is ReleaseSourceFamily.HDTV
    assert evidence.revision_rank == 2
    assert evidence.revision_markers == ("proper",)


def test_recovered_parent_quality_can_select_unique_duplicate_winner() -> None:
    lower = "Example.Series.S01.720p.WEB-DL/Example.Series.S01E01.mkv"
    higher = "Example.Series.S01.1080p.WEB-DL/Example.Series.S01E01.mkv"

    decision = _decision(lower, higher)

    assert decision.winner == higher
    assert decision.losers == (lower,)
    assert any(
        item == "release-quality-policy:unique release-quality dominance via resolution"
        for item in decision.evidence
    )


def test_parent_remux_and_filename_encode_remain_incomparable() -> None:
    encode = "Encode.1080p.BluRay/Example.Series.S01E01.mkv"
    remux = "Remux.1080p.BD-Remux/Example.Series.S01E01.mkv"

    decision = _decision(encode, remux)

    assert decision.winner is None
    assert decision.losers == ()
    assert "release-quality-policy:remux and encode candidates are incomparable" in (
        decision.evidence
    )


def test_duplicate_selection_is_input_order_deterministic_with_parent_evidence() -> (
    None
):
    lower = "Example.Series.S01.720p.WEB-DL/Example.Series.S01E01.mkv"
    higher = "Example.Series.S01.1080p.WEB-DL/Example.Series.S01E01.mkv"

    assert _decision(lower, higher) == _decision(higher, lower)
