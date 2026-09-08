from __future__ import annotations

import json

import pytest

from jellyfin_show_organizer import quarantine_contract
from jellyfin_show_organizer.apply_contract import ApplyContract
from jellyfin_show_organizer.apply_execution import PreparedApply
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.quarantine_contract import (
    QuarantineContractError,
    QuarantineGroup,
    QuarantineMember,
    QuarantineMemberRole,
    QuarantinePlan,
    QuarantineWinner,
    derive_quarantine_plan,
    load_quarantine_plan,
    render_quarantine_plan,
    validate_quarantine_plan_binding,
)

pytestmark = pytest.mark.local
PLAN_SHA = "a" * 64
REVIEW_SHA = "b" * 64
REVISION = "c" * 40


def _fingerprint(size: int) -> dict[str, object]:
    return {"size": size, "mtime_ns": size * 100, "sha256": None}


def _duplicate() -> dict[str, object]:
    return {
        "candidates": ["Show/winner.mkv", "Show/loser-a.mkv", "Show/loser-b.mkv"],
        "losers": ["Show/loser-a.mkv", "Show/loser-b.mkv"],
        "winner": "Show/winner.mkv",
        "destination_key": "show-s01e01",
    }


def _record(
    path: str,
    status: str,
    size: int,
    *,
    destination: str = "Show (2026)/Season 01/Show S01E01.mkv",
) -> dict[str, object]:
    return {
        "status": status,
        "source": {"relative_path": path, "fingerprint": _fingerprint(size)},
        "destination": destination,
        "duplicate": _duplicate(),
    }


def _manifest() -> dict[str, object]:
    return {
        "records": [
            _record("Show/winner.mkv", "matched", 10),
            _record("Show/loser-a.mkv", "duplicate", 11),
            _record("Show/loser-b.mkv", "duplicate", 12),
        ],
        "companions": [
            {
                "status": "duplicate",
                "relative_path": "Show/loser-a.en.srt",
                "source_video": "Show/loser-a.mkv",
                "destination": None,
                "fingerprint": _fingerprint(3),
            },
            {
                "status": "ignored",
                "relative_path": "Show/poster.jpg",
                "source_video": "Show/winner.mkv",
                "destination": None,
                "fingerprint": _fingerprint(4),
            },
        ],
    }


def _prepared() -> PreparedApply:
    return PreparedApply(
        contract=ApplyContract(plan_sha256=PLAN_SHA, groups=()),
        review_session_sha256=REVIEW_SHA,
        source_revision=REVISION,
    )


def _patch_hash(monkeypatch: pytest.MonkeyPatch, value: str = PLAN_SHA) -> None:
    monkeypatch.setattr(
        quarantine_contract, "manifest_plan_hash", lambda _manifest: value
    )


def test_derives_only_reviewed_duplicate_losers_and_bound_companions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_hash(monkeypatch)
    plan = derive_quarantine_plan(_manifest(), _prepared())

    assert plan.plan_sha256 == PLAN_SHA
    assert plan.review_session_sha256 == REVIEW_SHA
    assert plan.source_revision == REVISION
    assert len(plan.groups) == 2
    assert [group.members[0].source_relative_path for group in plan.groups] == [
        "Show/loser-a.mkv",
        "Show/loser-b.mkv",
    ]
    assert [member.role for member in plan.groups[0].members] == [
        QuarantineMemberRole.VIDEO,
        QuarantineMemberRole.COMPANION,
    ]
    assert plan.groups[0].members[1].source_relative_path == "Show/loser-a.en.srt"
    assert all(
        group.winner.source_relative_path == "Show/winner.mkv" for group in plan.groups
    )
    assert all(
        group.winner.organized_relative_path == "Show (2026)/Season 01/Show S01E01.mkv"
        for group in plan.groups
    )
    assert len({group.group_id for group in plan.groups}) == 2


def test_quarantine_plan_round_trip_and_exact_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_hash(monkeypatch)
    manifest = _manifest()
    plan = derive_quarantine_plan(manifest, _prepared())
    payload = render_quarantine_plan(plan)
    loaded = load_quarantine_plan(payload)

    assert payload.endswith(b"\n")
    assert loaded.canonical_bytes == plan.canonical_bytes
    assert loaded.sha256 == plan.sha256
    assert validate_quarantine_plan_binding(loaded, manifest, _prepared()) == plan

    tampered = json.loads(payload)
    tampered["groups"][0]["members"][0]["source_relative_path"] = "Show/other.mkv"
    supplied = load_quarantine_plan(json.dumps(tampered).encode())
    with pytest.raises(QuarantineContractError, match="does not match"):
        validate_quarantine_plan_binding(supplied, manifest, _prepared())


def test_derivation_rejects_wrong_plan_and_missing_or_invalid_duplicate_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_hash(monkeypatch, "d" * 64)
    with pytest.raises(QuarantineContractError, match="exact apply approval"):
        derive_quarantine_plan(_manifest(), _prepared())

    _patch_hash(monkeypatch)
    manifest = _manifest()
    manifest["records"] = [manifest["records"][0]]
    with pytest.raises(QuarantineContractError, match="no duplicate losers"):
        derive_quarantine_plan(manifest, _prepared())

    manifest = _manifest()
    loser = manifest["records"][1]
    loser["duplicate"] = {
        "candidates": ["Show/winner.mkv", "Show/loser-a.mkv", "Show/loser-a.mkv"],
        "losers": ["Show/loser-a.mkv"],
        "winner": "Show/winner.mkv",
        "destination_key": "show-s01e01",
    }
    with pytest.raises(QuarantineContractError, match="must be unique"):
        derive_quarantine_plan(manifest, _prepared())


def test_derivation_rejects_missing_winner_nonmatched_winner_and_destination_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_hash(monkeypatch)
    manifest = _manifest()
    manifest["records"] = manifest["records"][1:]
    with pytest.raises(QuarantineContractError, match="winner record is missing"):
        derive_quarantine_plan(manifest, _prepared())

    manifest = _manifest()
    manifest["records"][0]["status"] = "held"
    with pytest.raises(QuarantineContractError, match="approved matched"):
        derive_quarantine_plan(manifest, _prepared())

    manifest = _manifest()
    manifest["records"][1]["destination"] = "Wrong/Season 01/wrong.mkv"
    with pytest.raises(QuarantineContractError, match="share the reviewed destination"):
        derive_quarantine_plan(manifest, _prepared())


def test_derivation_rejects_winner_decision_drift_and_orphan_duplicate_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_hash(monkeypatch)
    manifest = _manifest()
    winner_duplicate = manifest["records"][0]["duplicate"]
    winner_duplicate["destination_key"] = "different-key"
    with pytest.raises(QuarantineContractError, match="disagree"):
        derive_quarantine_plan(manifest, _prepared())

    manifest = _manifest()
    manifest["companions"].append(
        {
            "status": "duplicate",
            "relative_path": "Other/orphan.srt",
            "source_video": "Other/no-video.mkv",
            "destination": None,
            "fingerprint": _fingerprint(5),
        }
    )
    with pytest.raises(QuarantineContractError, match="without a duplicate loser"):
        derive_quarantine_plan(manifest, _prepared())

    manifest = _manifest()
    manifest["companions"][0]["destination"] = "not-allowed.srt"
    with pytest.raises(QuarantineContractError, match="unexpectedly has"):
        derive_quarantine_plan(manifest, _prepared())


def test_artifact_loader_fails_closed_on_shape_and_member_errors() -> None:
    with pytest.raises(QuarantineContractError, match="invalid quarantine plan JSON"):
        load_quarantine_plan(b"not-json")
    with pytest.raises(QuarantineContractError, match="unexpected fields"):
        load_quarantine_plan(b'{"schema_version":1}')

    base = QuarantinePlan(
        schema_version=1,
        plan_sha256=PLAN_SHA,
        review_session_sha256=REVIEW_SHA,
        source_revision=REVISION,
        groups=(
            QuarantineGroup(
                group_id="q",
                duplicate_destination_key="key",
                winner=QuarantineWinner(
                    "Show/winner.mkv",
                    "Show/Season 01/winner.mkv",
                    SourceFingerprint(size=1, mtime_ns=1),
                ),
                members=(
                    QuarantineMember(
                        QuarantineMemberRole.VIDEO,
                        "Show/loser.mkv",
                        SourceFingerprint(size=2, mtime_ns=2),
                    ),
                ),
            ),
        ),
    )
    raw = json.loads(base.canonical_bytes)
    raw["groups"][0]["members"][0]["role"] = "delete"
    with pytest.raises(QuarantineContractError, match="role is invalid"):
        load_quarantine_plan(json.dumps(raw).encode())

    raw = json.loads(base.canonical_bytes)
    raw["groups"][0]["members"][0]["fingerprint"]["size"] = "bad"
    with pytest.raises(QuarantineContractError, match="size must be an integer"):
        load_quarantine_plan(json.dumps(raw).encode())


def test_quarantine_value_objects_reject_unsafe_internal_shapes() -> None:
    fp = SourceFingerprint(size=1, mtime_ns=1)
    with pytest.raises(ValueError, match="source cannot be empty"):
        QuarantineMember(QuarantineMemberRole.VIDEO, "", fp)
    with pytest.raises(ValueError, match="winner paths"):
        QuarantineWinner("", "organized.mkv", fp)
    with pytest.raises(ValueError, match="exactly one loser video"):
        QuarantineGroup(
            "q",
            "key",
            QuarantineWinner("winner.mkv", "organized.mkv", fp),
            (QuarantineMember(QuarantineMemberRole.COMPANION, "x.srt", fp),),
        )
    with pytest.raises(ValueError, match="source revision"):
        QuarantinePlan(1, PLAN_SHA, REVIEW_SHA, "short", ())
