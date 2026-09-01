from __future__ import annotations

import pytest

from jellyfin_show_organizer.duplicate_classifier import (
    DuplicateCandidate,
    DuplicatePreference,
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


def _candidate(
    source: str,
    *,
    size: int = 1_000,
    sha256: str | None = None,
    preference: DuplicatePreference | None = None,
) -> DuplicateCandidate:
    return DuplicateCandidate(
        operation_key=source,
        members=(source,),
        destination=_DESTINATION,
        logical_identity=_IDENTITY,
        fingerprint=SourceFingerprint(size=size, mtime_ns=1, sha256=sha256),
        preference=preference,
    )


def _decision(*candidates: DuplicateCandidate):
    (result,) = classify_duplicate_candidates(candidates)
    return result.decision


def test_release_quality_parser_records_only_structured_supported_signals() -> None:
    evidence = parse_release_quality("Example.Series.S01E01.1080p.WEB-DL.REPACK.v3.mkv")

    assert evidence.resolution == 1080
    assert evidence.source_family is ReleaseSourceFamily.WEB_DL
    assert evidence.remux is False
    assert evidence.revision_rank == 3
    assert evidence.revision_markers == ("repack", "v3")
    assert evidence.errors == ()


def test_1080p_uniquely_beats_720p_with_compatible_release_evidence() -> None:
    lower = _candidate("Example.Series.S01E01.720p.WEB-DL.mkv")
    higher = _candidate("Example.Series.S01E01.1080p.WEB-DL.mkv")

    decision = _decision(lower, higher)

    assert decision.winner == higher.operation_key
    assert decision.losers == (lower.operation_key,)
    assert decision.confidence == 0.8
    assert any(
        item == "release-quality-policy:unique release-quality dominance via resolution"
        for item in decision.evidence
    )
    assert any(
        item.startswith(f"release-quality:{higher.operation_key}:resolution=1080p;")
        for item in decision.evidence
    )
    assert decision.evidence[-1] == (
        "non-selected candidates are duplicate/non-moving only; no deletion is authorized"
    )


def test_remux_and_encode_are_incomparable() -> None:
    encode = _candidate("Example.Series.S01E01.1080p.BluRay.x264.mkv")
    remux = _candidate("Example.Series.S01E01.1080p.BluRay.REMUX.mkv")

    decision = _decision(encode, remux)

    assert decision.winner is None
    assert decision.losers == ()
    assert "release-quality-policy:remux and encode candidates are incomparable" in (
        decision.evidence
    )


@pytest.mark.parametrize(
    ("base_name", "preferred_name"),
    [
        (
            "Example.Series.S01E01.1080p.WEB-DL.mkv",
            "Example.Series.S01E01.1080p.WEB-DL.REPACK.mkv",
        ),
        (
            "Example.Series.S01E01.1080p.WEB-DL.v2.mkv",
            "Example.Series.S01E01.1080p.WEB-DL.v3.mkv",
        ),
    ],
)
def test_explicit_repack_or_higher_version_can_win_when_other_dimensions_match(
    base_name: str,
    preferred_name: str,
) -> None:
    base = _candidate(base_name)
    preferred = _candidate(preferred_name)

    decision = _decision(base, preferred)

    assert decision.winner == preferred.operation_key
    assert decision.losers == (base.operation_key,)
    assert (
        "release-quality-policy:unique release-quality dominance via revision"
        in decision.evidence
    )


def test_equal_quality_ties_remain_unresolved() -> None:
    first = _candidate("Example.Series.S01E01.1080p.WEB-DL.GROUP-A.mkv")
    second = _candidate("Example.Series.S01E01.1080p.WEB-DL.GROUP-B.mkv")

    decision = _decision(first, second)

    assert decision.winner is None
    assert decision.losers == ()
    assert any("no unique candidate dominates" in item for item in decision.evidence)


def test_incomplete_release_metadata_remains_unresolved() -> None:
    incomplete = _candidate("Example.Series.S01E01.1080p.mkv")
    complete = _candidate("Example.Series.S01E01.720p.WEB-DL.mkv")

    decision = _decision(incomplete, complete)

    assert decision.winner is None
    assert "release-quality-policy:release-quality evidence is incomplete" in (
        decision.evidence
    )


def test_conflicting_source_families_remain_unresolved() -> None:
    web = _candidate("Example.Series.S01E01.1080p.WEB-DL.mkv")
    bluray = _candidate("Example.Series.S01E01.720p.BluRay.mkv")

    decision = _decision(web, bluray)

    assert decision.winner is None
    assert "release-quality-policy:release source families are incomparable" in (
        decision.evidence
    )


def test_cross_dimension_tradeoff_remains_unresolved() -> None:
    resolution = _candidate("Example.Series.S01E01.1080p.WEB-DL.mkv")
    revision = _candidate("Example.Series.S01E01.720p.WEB-DL.REPACK.mkv")

    decision = _decision(resolution, revision)

    assert decision.winner is None
    assert any("no unique candidate dominates" in item for item in decision.evidence)


def test_file_size_never_breaks_an_equal_quality_tie() -> None:
    small = _candidate(
        "Example.Series.S01E01.1080p.WEB-DL.SMALL.mkv",
        size=100,
    )
    large = _candidate(
        "Example.Series.S01E01.1080p.WEB-DL.LARGE.mkv",
        size=100_000_000,
    )

    decision = _decision(small, large)

    assert decision.winner is None
    assert decision.losers == ()


def test_exact_hash_equivalence_has_authority_over_release_quality() -> None:
    lower_quality = _candidate(
        "A.Example.Series.S01E01.720p.WEB-DL.mkv",
        sha256="a" * 64,
    )
    higher_quality = _candidate(
        "B.Example.Series.S01E01.1080p.WEB-DL.mkv",
        sha256="a" * 64,
    )

    decision = _decision(higher_quality, lower_quality)

    assert decision.winner == lower_quality.operation_key
    assert decision.confidence == 1.0
    assert "exact SHA-256" in decision.evidence[0]
    assert any(item.startswith("release-quality:") for item in decision.evidence)


def test_explicit_preference_has_authority_over_automatic_release_quality() -> None:
    preferred = _candidate(
        "Example.Series.S01E01.720p.WEB-DL.mkv",
        preference=DuplicatePreference(
            rank=50,
            reasons=("reviewed local preference",),
        ),
    )
    automatic_quality = _candidate("Example.Series.S01E01.1080p.WEB-DL.mkv")

    decision = _decision(preferred, automatic_quality)

    assert decision.winner == preferred.operation_key
    assert decision.confidence == 0.9
    assert "reviewed local preference" in decision.evidence


def test_tied_explicit_preferences_block_automatic_quality_selection() -> None:
    lower = _candidate(
        "Example.Series.S01E01.720p.WEB-DL.mkv",
        preference=DuplicatePreference(rank=10, reasons=("reviewed tie",)),
    )
    higher = _candidate(
        "Example.Series.S01E01.1080p.WEB-DL.mkv",
        preference=DuplicatePreference(rank=10, reasons=("reviewed tie",)),
    )

    decision = _decision(lower, higher)

    assert decision.winner is None
    assert any(
        "not allowed to override tied explicit preferences" in item
        for item in decision.evidence
    )
