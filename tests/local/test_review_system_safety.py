from __future__ import annotations

import json
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
    compile_active_overrides,
    load_review_contract,
    load_review_contract_payload,
)
from jellyfin_show_organizer.review_identity import (
    ReviewCandidateBinding,
    ReviewFingerprint,
    ReviewMemberBinding,
    duplicate_candidate_set_hash,
)
from jellyfin_show_organizer.review_session import (
    REVIEW_SESSION_SCHEMA_VERSION,
    ReviewCollisionClass,
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


def _session_item_payload(**updates: object) -> bytes:
    item: dict[str, object] = {
        "action": "garbage",
        "candidate_set_sha256": None,
        "candidates": [],
        "collision_class": None,
        "data": {},
        "duplicate_ref": None,
        "kind": "held",
        "review_ref": "held-0123456789abcdef",
        "show_key": "Fabricated Series",
        "source": "Fabricated Series/Held.mkv",
        "source_binding_sha256": "c" * 64,
        "state": "answered",
    }
    item.update(updates)
    payload = {
        "schema_version": REVIEW_SESSION_SCHEMA_VERSION,
        "plan_sha256": "a" * 64,
        "base_override_snapshot": "b" * 64,
        "base_override_toml": "schema_version = 4\n",
        "approved_scope_refs": [],
        "items": [item],
    }
    return (json.dumps(payload, sort_keys=True) + "\n").encode()


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


@pytest.mark.parametrize("schema_version", [1, 2, 3, 4])
def test_review_loader_preserves_legacy_override_schemas(
    tmp_path: Path,
    schema_version: int,
) -> None:
    path = tmp_path / f"legacy-v{schema_version}.toml"
    payload = f"schema_version = {schema_version}\n".encode()
    path.write_bytes(payload)

    path_catalog = load_review_contract(path)
    payload_catalog = load_review_contract_payload(payload)

    assert path_catalog.schema_version == schema_version
    assert payload_catalog.schema_version == schema_version
    assert path_catalog.snapshot_id == payload_catalog.snapshot_id


def test_schema5_duplicate_group_compiles_authoritative_planner_preference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reviewed.toml"
    path.write_text(
        """schema_version = 5
review_session_sha256 = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
review_base_plan_sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
review_base_override_snapshot = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

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
        base_override_toml="schema_version = 4\n",
        items=(
            ReviewSessionItem(
                review_ref="held-0123456789abcdef",
                kind=ReviewItemKind.HELD,
                state=ReviewItemState.DEFERRED,
                show_key="Fabricated Series",
                source="Fabricated Series/Held.mkv",
                source_binding_sha256="c" * 64,
                action="defer",
            ),
        ),
    )

    restored = load_review_session(render_review_session(session))

    assert restored == session
    assert restored.sha256 == session.sha256


def test_session_loader_rejects_unknown_answered_held_action() -> None:
    with pytest.raises(ValueError, match="invalid action"):
        load_review_session(_session_item_payload())


@pytest.mark.parametrize(
    ("action", "data"),
    [
        ("keep_held", {"unexpected": True}),
        ("episode", {}),
        ("special", {"reviewed_episode": {"source": "Fabricated Series/Held.mkv"}}),
        ("extra", {"extra": {"source": "Fabricated Series/Held.mkv"}}),
    ],
)
def test_session_loader_rejects_invalid_held_action_data(
    action: str,
    data: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        load_review_session(_session_item_payload(action=action, data=data))


def test_session_loader_rejects_winner_for_destination_conflict() -> None:
    with pytest.raises(ValueError, match="destination-conflict"):
        load_review_session(
            _session_item_payload(
                action="select_winner",
                candidate_set_sha256="d" * 64,
                candidates=["Fabricated/A.mkv", "Fabricated/B.mkv"],
                collision_class=ReviewCollisionClass.DESTINATION_CONFLICT.value,
                data={
                    "active_action": "select_winner",
                    "winner": "Fabricated/A.mkv",
                },
                duplicate_ref="duplicate-0123456789abcdef",
                kind="duplicate",
                review_ref="duplicate-0123456789abcdef",
                source=None,
                source_binding_sha256=None,
            )
        )


def test_same_identity_duplicate_winner_does_not_depend_on_evidence_text() -> None:
    item = ReviewSessionItem(
        review_ref="duplicate-0123456789abcdef",
        kind=ReviewItemKind.DUPLICATE,
        state=ReviewItemState.ANSWERED,
        show_key="Fabricated Series",
        duplicate_ref="duplicate-0123456789abcdef",
        candidate_set_sha256="d" * 64,
        candidates=("Fabricated/A.mkv", "Fabricated/B.mkv"),
        collision_class=ReviewCollisionClass.SAME_LOGICAL_IDENTITY,
        action="select_winner",
        data_json=json.dumps(
            {
                "active_action": "select_winner",
                "winner": "Fabricated/B.mkv",
            }
        ),
    )

    assert item.collision_class is ReviewCollisionClass.SAME_LOGICAL_IDENTITY
    assert item.data["winner"] == "Fabricated/B.mkv"


def test_keep_held_compiler_preserves_existing_hold(tmp_path: Path) -> None:
    base_path = tmp_path / "base.toml"
    base_payload = b'''schema_version = 4

[[source_holds]]
source = "Fabricated Series/Held.mkv"
reasons = ["reviewed leave in place"]
'''
    base_path.write_bytes(base_payload)
    base = load_review_contract(base_path)
    session = ReviewSession(
        schema_version=REVIEW_SESSION_SCHEMA_VERSION,
        plan_sha256="a" * 64,
        base_override_snapshot=base.snapshot_id,
        base_override_toml=base_payload.decode(),
        items=(
            ReviewSessionItem(
                review_ref="held-0123456789abcdef",
                kind=ReviewItemKind.HELD,
                state=ReviewItemState.ANSWERED,
                show_key="Fabricated Series",
                source="Fabricated Series/Held.mkv",
                source_binding_sha256="c" * 64,
                action="keep_held",
            ),
        ),
    )

    compiled = load_review_contract_payload(compile_active_overrides(session))

    assert isinstance(compiled, ReviewContractCatalog)
    assert len(compiled.source_holds) == 1
    assert compiled.source_holds[0].source == "Fabricated Series/Held.mkv"


def test_atomic_new_output_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    atomic_write_new(path, b"first\n")

    with pytest.raises(FileExistsError):
        atomic_write_new(path, b"second\n")

    assert path.read_bytes() == b"first\n"
