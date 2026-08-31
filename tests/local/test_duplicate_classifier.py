import pytest

from jellyfin_show_organizer.duplicate_classifier import (
    DuplicateCandidate,
    DuplicateDisposition,
    DuplicatePreference,
    classify_duplicate_candidates,
)
from jellyfin_show_organizer.models import SourceFingerprint

pytestmark = pytest.mark.local


def _fingerprint(
    *,
    size: int = 1_000,
    mtime_ns: int = 5_000,
    sha256: str | None = None,
) -> SourceFingerprint:
    return SourceFingerprint(size=size, mtime_ns=mtime_ns, sha256=sha256)


def _candidate(
    operation_key: str,
    *,
    destination: str = "Example Series/Season 01/Example Series - S01E01.mkv",
    logical_identity: str = "provider:100:S01E01",
    fingerprint: SourceFingerprint | None = None,
    preference: DuplicatePreference | None = None,
    members: tuple[str, ...] | None = None,
) -> DuplicateCandidate:
    return DuplicateCandidate(
        operation_key=operation_key,
        members=members or (f"incoming/{operation_key}.mkv",),
        destination=destination,
        logical_identity=logical_identity,
        fingerprint=fingerprint or _fingerprint(),
        preference=preference,
    )


def test_single_destination_candidate_does_not_create_duplicate_decision():
    assert classify_duplicate_candidates([_candidate("only-source")]) == ()


def test_case_and_separator_equivalent_destinations_are_grouped_consistently():
    first = _candidate(
        "source-a",
        destination="Example Series\\Season 01\\Episode.mkv",
        fingerprint=_fingerprint(sha256="a" * 64),
    )
    second = _candidate(
        "source-b",
        destination="example series/season 01/episode.MKV",
        fingerprint=_fingerprint(sha256="a" * 64),
    )

    (result,) = classify_duplicate_candidates([second, first])

    assert result.disposition is DuplicateDisposition.DUPLICATE
    assert result.decision.destination_key == "example series/season 01/episode.mkv"
    assert result.decision.candidates == ("source-a", "source-b")


def test_exact_hash_duplicates_choose_stable_representative_only_as_tiebreaker():
    first = _candidate(
        "alpha-source",
        fingerprint=_fingerprint(size=2_000, mtime_ns=20, sha256="b" * 64),
    )
    second = _candidate(
        "beta-source",
        fingerprint=_fingerprint(size=2_000, mtime_ns=99, sha256="b" * 64),
    )

    (result,) = classify_duplicate_candidates([second, first])

    assert result.disposition is DuplicateDisposition.DUPLICATE
    assert result.decision.winner == "alpha-source"
    assert result.decision.losers == ("beta-source",)
    assert result.decision.confidence == 1.0
    assert "exact SHA-256" in result.decision.evidence[0]


def test_size_and_timestamp_metadata_never_select_a_winner_by_themselves():
    first = _candidate("small-old", fingerprint=_fingerprint(size=500, mtime_ns=1))
    second = _candidate("large-new", fingerprint=_fingerprint(size=5_000, mtime_ns=999))

    (result,) = classify_duplicate_candidates([first, second])

    assert result.disposition is DuplicateDisposition.DUPLICATE
    assert result.decision.winner is None
    assert result.decision.losers == ()
    assert result.decision.confidence == 0.5


def test_unique_explicit_preference_rank_can_select_winner():
    lower = _candidate(
        "web-source",
        fingerprint=_fingerprint(sha256="c" * 64),
        preference=DuplicatePreference(
            rank=10,
            reasons=("configured source preference: web",),
        ),
    )
    higher = _candidate(
        "disc-source",
        fingerprint=_fingerprint(sha256="d" * 64),
        preference=DuplicatePreference(
            rank=20,
            reasons=("configured source preference: disc",),
        ),
    )

    (result,) = classify_duplicate_candidates([lower, higher])

    assert result.decision.winner == "disc-source"
    assert result.decision.losers == ("web-source",)
    assert result.decision.confidence == 0.9
    assert "configured source preference: disc" in result.decision.evidence


def test_tied_preferences_fail_closed_and_one_explicit_preference_can_win():
    tied_a = _candidate(
        "source-a",
        preference=DuplicatePreference(rank=10, reasons=("same explicit rank",)),
    )
    tied_b = _candidate(
        "source-b",
        preference=DuplicatePreference(rank=10, reasons=("same explicit rank",)),
    )

    (tied_result,) = classify_duplicate_candidates([tied_a, tied_b])
    assert tied_result.decision.winner is None
    assert tied_result.decision.losers == ()

    missing = _candidate("source-c")
    (missing_result,) = classify_duplicate_candidates([tied_a, missing])
    assert missing_result.decision.winner == "source-a"
    assert missing_result.decision.losers == ("source-c",)

    unranked = _candidate("source-d")
    (unranked_result,) = classify_duplicate_candidates([missing, unranked])
    assert unranked_result.decision.winner is None
    assert unranked_result.decision.losers == ()


def test_destination_convergence_across_logical_identities_is_suspicious():
    first = _candidate(
        "episode-one",
        logical_identity="provider:100:S01E01",
        fingerprint=_fingerprint(sha256="e" * 64),
    )
    second = _candidate(
        "episode-two",
        logical_identity="provider:100:S01E02",
        fingerprint=_fingerprint(sha256="e" * 64),
    )

    (result,) = classify_duplicate_candidates([first, second])

    assert result.disposition is DuplicateDisposition.SUSPICIOUS
    assert result.decision.winner is None
    assert result.decision.losers == ()
    assert result.decision.confidence == 0.0
    assert result.decision.evidence[0] == (
        "destination convergence spans multiple logical identities"
    )


def test_multi_episode_video_and_sidecars_remain_one_operation_group():
    first = _candidate(
        "release-a",
        fingerprint=_fingerprint(sha256="f" * 64),
        members=(
            "incoming/episode-01-02.mkv",
            "incoming/episode-01-02.en.srt",
            "incoming/episode-01-02.forced.srt",
        ),
    )
    second = _candidate(
        "release-b",
        fingerprint=_fingerprint(sha256="f" * 64),
        members=(
            "other/episode-01-02.mkv",
            "other/episode-01-02.en.srt",
        ),
    )

    (result,) = classify_duplicate_candidates([first, second])

    assert result.decision.winner == "release-a"
    assert result.decision.losers == ("release-b",)
    assert result.candidates[0].members == first.members
    assert result.candidates[1].members == second.members


def test_result_is_deterministic_regardless_of_candidate_input_order():
    candidates = [
        _candidate("source-c", fingerprint=_fingerprint(sha256="1" * 64)),
        _candidate("source-a", fingerprint=_fingerprint(sha256="1" * 64)),
        _candidate("source-b", fingerprint=_fingerprint(sha256="1" * 64)),
    ]

    forward = classify_duplicate_candidates(candidates)
    reverse = classify_duplicate_candidates(reversed(candidates))

    assert forward == reverse
    assert forward[0].decision.winner == "source-a"
    assert forward[0].decision.losers == ("source-b", "source-c")


def test_duplicate_operation_keys_are_rejected_case_insensitively():
    with pytest.raises(ValueError, match="operation_key supplied more than once"):
        classify_duplicate_candidates(
            [
                _candidate("Release-A"),
                _candidate("release-a"),
            ]
        )


def test_operation_members_are_indivisible_and_case_insensitively_unique():
    with pytest.raises(ValueError, match="members must be unique"):
        _candidate(
            "release-a",
            members=("incoming/Episode.en.srt", "incoming/episode.EN.srt"),
        )
