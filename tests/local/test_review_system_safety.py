from __future__ import annotations

from pathlib import Path

import pytest

from jellyfin_show_organizer.duplicate_classifier import (
    DuplicateCandidate,
    DuplicatePreference,
    classify_duplicate_candidates,
)
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.review_contract import (
    DuplicateGroupAction,
    ReviewContractCatalog,
    load_review_contract,
)
from jellyfin_show_organizer.review_identity import (
    ReviewCandidateBinding,
    ReviewFingerprint,
    ReviewMemberBinding,
    duplicate_candidate_set_hash,
)
from jellyfin_show_organizer.review_session import (
    REVIEW_SESSION_SCHEMA_VERSION,
    ReviewItemKind,
    ReviewItemState,
    ReviewSession,
    ReviewSessionItem,
    atomic_write_new,
    load_review_session,
    render_review_session,
)

pytestmark = pytest.mark.local


def _candidate(
    name: str,
    *,
    preference: DuplicatePreference | None = None,
) -> DuplicateCandidate:
    return DuplicateCandidate(
        operation_key=f"Fabricated Series/{name}.mkv",
        members=(f"Fabricated Series/{name}.mkv",),
        destination="Fabricated Series/Season 01/Fabricated Series S01E01.mkv",
        logical_identity="tvmaze:4242:episode:9001",
        fingerprint=SourceFingerprint(
            size=100,
            mtime_ns=200,
            sha256="a" * 64,
        ),
        preference=preference,
    )


def test_explicit_duplicate_preference_outranks_exact_hash_equivalence() -> None:
    first = _candidate("A")
    second = _candidate(
        "B",
        preference=DuplicatePreference(
            rank=100,
            reasons=("human reviewed winner",),
        ),
    )

    result = classify_duplicate_candidates((first, second))[0]

    assert result.decision.winner == second.operation_key
    assert "human reviewed winner" in result.decision.evidence


def test_candidate_set_hash_includes_companion_fingerprints() -> None:
    source = ReviewMemberBinding(
        path="Fabricated Series/Fabricated.mkv",
        fingerprint=ReviewFingerprint(size=100, mtime_ns=200, sha256="a" * 64),
    )
    first = ReviewCandidateBinding(
        source=source,
        companions=(
            ReviewMemberBinding(
                path="Fabricated Series/Fabricated.en.srt",
                fingerprint=ReviewFingerprint(
                    size=10,
                    mtime_ns=20,
                    sha256="b" * 64,
                ),
            ),
        ),
    )
    second = ReviewCandidateBinding(
        source=source,
        companions=(
            ReviewMemberBinding(
                path="Fabricated Series/Fabricated.en.srt",
                fingerprint=ReviewFingerprint(
                    size=11,
                    mtime_ns=20,
                    sha256="c" * 64,
                ),
            ),
        ),
    )

    assert duplicate_candidate_set_hash("destination", (first,)) != (
        duplicate_candidate_set_hash("destination", (second,))
    )


def test_schema5_duplicate_group_compiles_authoritative_planner_preference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reviewed.toml"
    path.write_text(
        """schema_version = 5
review_session_sha256 = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"

[[duplicate_group_decisions]]
duplicate_ref = "duplicate-0123456789abcdef"
candidate_set_sha256 = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
candidates = ["Fabricated/A.mkv", "Fabricated/B.mkv"]
action = "select_winner"
winner = "Fabricated/B.mkv"
reasons = ["human reviewed"]
""",
        encoding="utf-8",
    )

    catalog = load_review_contract(path)

    assert isinstance(catalog, ReviewContractCatalog)
    assert catalog.review_session_sha256 == "d" * 64
    assert len(catalog.duplicate_group_decisions) == 1
    decision = catalog.duplicate_group_decisions[0]
    assert decision.action is DuplicateGroupAction.SELECT_WINNER
    preference = catalog.duplicate_preference_for("Fabricated/B.mkv")
    assert preference is not None
    assert preference.rank == 1_000_000


def test_review_session_roundtrip_is_deterministic() -> None:
    session = ReviewSession(
        schema_version=REVIEW_SESSION_SCHEMA_VERSION,
        plan_sha256="a" * 64,
        base_override_snapshot="b" * 64,
        items=(
            ReviewSessionItem(
                review_ref="held-0123456789abcdef",
                kind=ReviewItemKind.HELD,
                state=ReviewItemState.DEFERRED,
                show_key="Fabricated Series",
                source="Fabricated Series/Held.mkv",
                action="defer",
            ),
        ),
    )

    restored = load_review_session(render_review_session(session))

    assert restored == session
    assert restored.sha256 == session.sha256


def test_atomic_new_output_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    atomic_write_new(path, b"first\n")

    with pytest.raises(FileExistsError):
        atomic_write_new(path, b"second\n")

    assert path.read_bytes() == b"first\n"
