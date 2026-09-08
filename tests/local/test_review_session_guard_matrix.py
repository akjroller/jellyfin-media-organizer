from __future__ import annotations

import json
from dataclasses import replace

import pytest

from jellyfin_show_organizer.models import (
    MatchEvidence,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.review_session import (
    REVIEW_SESSION_SCHEMA_VERSION,
    ReviewCollisionClass,
    ReviewItemKind,
    ReviewItemState,
    ReviewSession,
    ReviewSessionItem,
    build_review_session,
    load_review_session,
    manifest_override_snapshot,
    render_review_session,
)
from jellyfin_show_organizer.schema import PLAN_SCHEMA_VERSION, plan_to_manifest

pytestmark = pytest.mark.local

HELD_SOURCE = "Fabricated Series/Held.mkv"
FIRST = "Fabricated Series/A.mkv"
SECOND = "Fabricated Series/B.mkv"
HELD_REF = "held-0123456789abcdef"
DUP_REF = "duplicate-0123456789abcdef"

HELD_PENDING = ReviewSessionItem(
    review_ref=HELD_REF,
    kind=ReviewItemKind.HELD,
    state=ReviewItemState.PENDING,
    show_key="Fabricated Series",
    source=HELD_SOURCE,
    source_binding_sha256="c" * 64,
)
DUP_PENDING = ReviewSessionItem(
    review_ref=DUP_REF,
    kind=ReviewItemKind.DUPLICATE,
    state=ReviewItemState.PENDING,
    show_key="Fabricated Series",
    duplicate_ref=DUP_REF,
    candidate_set_sha256="d" * 64,
    candidates=(FIRST, SECOND),
    collision_class=ReviewCollisionClass.SAME_LOGICAL_IDENTITY,
)
BASE_SESSION = ReviewSession(
    schema_version=REVIEW_SESSION_SCHEMA_VERSION,
    plan_sha256="a" * 64,
    base_override_snapshot="b" * 64,
    base_override_toml="schema_version = 4\n",
    items=(HELD_PENDING,),
)


def _session_payload(session: ReviewSession = BASE_SESSION) -> dict[str, object]:
    return json.loads(render_review_session(session))


def _load_payload(payload: dict[str, object]) -> ReviewSession:
    return load_review_session((json.dumps(payload) + "\n").encode())


def test_session_rejects_invalid_schema_hashes_base_and_duplicate_refs() -> None:
    with pytest.raises(ValueError, match="unsupported review session schema version"):
        replace(BASE_SESSION, schema_version=999)
    with pytest.raises(ValueError, match="plan_sha256 must contain 64 hex"):
        replace(BASE_SESSION, plan_sha256="short")
    with pytest.raises(ValueError, match="plan_sha256 must contain 64 hex"):
        replace(BASE_SESSION, plan_sha256="z" * 64)
    with pytest.raises(ValueError, match="base_snapshot|base_override_snapshot"):
        replace(BASE_SESSION, base_override_snapshot="short")
    with pytest.raises(ValueError, match="base override TOML"):
        replace(BASE_SESSION, base_override_toml="")
    with pytest.raises(ValueError, match="refs must be unique"):
        replace(BASE_SESSION, items=(HELD_PENDING, HELD_PENDING))


def test_session_rejects_invalid_approved_scope() -> None:
    answered = replace(
        HELD_PENDING,
        state=ReviewItemState.ANSWERED,
        action="keep_held",
    )
    two = ReviewSession(
        schema_version=REVIEW_SESSION_SCHEMA_VERSION,
        plan_sha256="a" * 64,
        base_override_snapshot="b" * 64,
        base_override_toml="schema_version = 4\n",
        items=(answered, DUP_PENDING),
    )
    with pytest.raises(ValueError, match="unique and sorted"):
        replace(two, approved_scope_refs=(HELD_REF, HELD_REF))
    with pytest.raises(ValueError, match="unknown review item"):
        replace(two, approved_scope_refs=("held-ffffffffffffffff",))
    with pytest.raises(ValueError, match="only answered items"):
        replace(two, approved_scope_refs=(DUP_REF,))


def test_item_rejects_missing_identity_and_invalid_kind_fields() -> None:
    with pytest.raises(ValueError, match="require review_ref and show_key"):
        replace(HELD_PENDING, review_ref="")
    with pytest.raises(ValueError, match="require review_ref and show_key"):
        replace(HELD_PENDING, show_key="")
    with pytest.raises(
        ValueError, match="held review item has invalid identity fields"
    ):
        replace(HELD_PENDING, source=None)
    with pytest.raises(
        ValueError, match="held review item has invalid identity fields"
    ):
        replace(HELD_PENDING, duplicate_ref=DUP_REF)
    with pytest.raises(
        ValueError, match="duplicate review item has invalid identity fields"
    ):
        replace(DUP_PENDING, source=FIRST)
    with pytest.raises(
        ValueError, match="duplicate review item has invalid identity fields"
    ):
        replace(DUP_PENDING, candidates=(FIRST,))
    with pytest.raises(ValueError, match="candidate_set_sha256 must contain 64 hex"):
        replace(DUP_PENDING, candidate_set_sha256="short")


def test_item_rejects_invalid_json_and_state_payloads() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        replace(HELD_PENDING, data_json="{")
    with pytest.raises(ValueError, match="JSON object"):
        replace(HELD_PENDING, data_json="[]")
    with pytest.raises(ValueError, match="pending review items cannot carry an action"):
        replace(HELD_PENDING, action="keep_held")
    with pytest.raises(
        ValueError, match="pending review items cannot carry decision data"
    ):
        replace(HELD_PENDING, data_json='{"x":1}')
    with pytest.raises(ValueError, match="must carry action='defer'"):
        replace(HELD_PENDING, state=ReviewItemState.DEFERRED, action="keep_held")
    with pytest.raises(
        ValueError, match="deferred review items cannot carry decision data"
    ):
        replace(
            HELD_PENDING,
            state=ReviewItemState.DEFERRED,
            action="defer",
            data_json='{"x":1}',
        )
    with pytest.raises(ValueError, match="answered review items require an action"):
        replace(HELD_PENDING, state=ReviewItemState.ANSWERED)


def test_duplicate_answer_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="invalid action"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="garbage",
        )
    with pytest.raises(ValueError, match="active_action='keep_all'"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="keep_all",
            data_json='{"active_action":"select_winner"}',
        )
    with pytest.raises(ValueError, match="active_action='select_winner'"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="select_winner",
            data_json=json.dumps({"active_action": "keep_all", "winner": FIRST}),
        )
    with pytest.raises(ValueError, match="winner string"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="select_winner",
            data_json=json.dumps({"active_action": "select_winner", "winner": 1}),
        )
    with pytest.raises(ValueError, match="one reviewed candidate"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="select_winner",
            data_json=json.dumps(
                {"active_action": "select_winner", "winner": "Fabricated/C.mkv"}
            ),
        )


def test_quarantine_candidate_validation_fails_closed() -> None:
    base = {
        "active_action": "select_winner",
        "winner": FIRST,
        "quarantine_candidates": [SECOND],
    }
    with pytest.raises(ValueError, match="non-empty quarantine_candidates"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="quarantine_candidate",
            data_json=json.dumps({**base, "quarantine_candidates": []}),
        )
    with pytest.raises(ValueError, match="must contain strings"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="quarantine_candidate",
            data_json=json.dumps({**base, "quarantine_candidates": [2]}),
        )
    with pytest.raises(ValueError, match="must be unique"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="quarantine_candidate",
            data_json=json.dumps({**base, "quarantine_candidates": [SECOND, SECOND]}),
        )
    with pytest.raises(ValueError, match="winner cannot also"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="quarantine_candidate",
            data_json=json.dumps({**base, "quarantine_candidates": [FIRST]}),
        )
    with pytest.raises(ValueError, match="must be reviewed candidates"):
        replace(
            DUP_PENDING,
            state=ReviewItemState.ANSWERED,
            action="quarantine_candidate",
            data_json=json.dumps(
                {**base, "quarantine_candidates": ["Fabricated/C.mkv"]}
            ),
        )
    with pytest.raises(ValueError, match="destination-conflict"):
        replace(
            DUP_PENDING,
            collision_class=ReviewCollisionClass.DESTINATION_CONFLICT,
            state=ReviewItemState.ANSWERED,
            action="select_winner",
            data_json=json.dumps({"active_action": "select_winner", "winner": FIRST}),
        )


def test_held_answer_validation_requires_exact_source_and_show_metadata() -> None:
    episode = {"source": HELD_SOURCE}
    extra = {"source": HELD_SOURCE}
    with pytest.raises(ValueError, match="reviewed_episode must be an object"):
        replace(
            HELD_PENDING,
            state=ReviewItemState.ANSWERED,
            action="episode",
            data_json=json.dumps({"reviewed_episode": "bad", "show": {}}),
        )
    with pytest.raises(ValueError, match="reviewed_episode.source must be a string"):
        replace(
            HELD_PENDING,
            state=ReviewItemState.ANSWERED,
            action="episode",
            data_json=json.dumps({"reviewed_episode": {}, "show": {}}),
        )
    with pytest.raises(ValueError, match="does not match"):
        replace(
            HELD_PENDING,
            state=ReviewItemState.ANSWERED,
            action="special",
            data_json=json.dumps(
                {"reviewed_episode": {"source": "Other.mkv"}, "show": {}}
            ),
        )
    with pytest.raises(ValueError, match="show metadata must be an object"):
        replace(
            HELD_PENDING,
            state=ReviewItemState.ANSWERED,
            action="episode",
            data_json=json.dumps({"reviewed_episode": episode, "show": "bad"}),
        )
    with pytest.raises(ValueError, match="extra must be an object"):
        replace(
            HELD_PENDING,
            state=ReviewItemState.ANSWERED,
            action="extra",
            data_json=json.dumps({"extra": "bad", "show": {}}),
        )
    with pytest.raises(ValueError, match="extra show metadata must be an object"):
        replace(
            HELD_PENDING,
            state=ReviewItemState.ANSWERED,
            action="extra",
            data_json=json.dumps({"extra": extra, "show": "bad"}),
        )


def test_session_transitions_and_partial_scope_guards() -> None:
    with pytest.raises(ValueError, match="requested review ref"):
        BASE_SESSION.item("held-ffffffffffffffff")
    with pytest.raises(ValueError, match="back to pending"):
        BASE_SESSION.with_answer(
            HELD_REF,
            state=ReviewItemState.PENDING,
            action="keep_held",
        )
    with pytest.raises(ValueError, match="review ref was not found"):
        BASE_SESSION.with_answer(
            "held-ffffffffffffffff",
            state=ReviewItemState.ANSWERED,
            action="keep_held",
        )
    with pytest.raises(ValueError, match="non-empty explicit scope"):
        BASE_SESSION.with_approved_scope(())
    with pytest.raises(ValueError, match="unanswered item"):
        BASE_SESSION.with_approved_scope((HELD_REF,))

    answered = BASE_SESSION.with_answer(
        HELD_REF,
        state=ReviewItemState.ANSWERED,
        action="keep_held",
    )
    approved = replace(
        ReviewSession(
            schema_version=REVIEW_SESSION_SCHEMA_VERSION,
            plan_sha256="a" * 64,
            base_override_snapshot="b" * 64,
            base_override_toml="schema_version = 4\n",
            items=(answered.items[0], DUP_PENDING),
        ),
        approved_scope_refs=(HELD_REF,),
    )
    assert approved.approved_partial
    assert approved.usable_for_planning
    deferred = approved.with_answer(
        HELD_REF,
        state=ReviewItemState.DEFERRED,
        action="defer",
    )
    assert not deferred.approved_scope_refs
    assert not deferred.usable_for_planning


def test_session_loader_rejects_malformed_root_contracts() -> None:
    with pytest.raises(ValueError, match="invalid review session JSON"):
        load_review_session(b"{")
    with pytest.raises(ValueError, match="root must be an object"):
        load_review_session(b"[]")

    payload = _session_payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected fields"):
        _load_payload(payload)

    payload = _session_payload()
    payload["items"] = {}
    with pytest.raises(ValueError, match="items must be an array"):
        _load_payload(payload)

    payload = _session_payload()
    payload["approved_scope_refs"] = [1]
    with pytest.raises(ValueError, match="scope refs must be strings"):
        _load_payload(payload)


def test_session_loader_rejects_malformed_item_fields() -> None:
    payload = _session_payload()
    payload["items"] = ["bad"]
    with pytest.raises(ValueError, match="item must be an object"):
        _load_payload(payload)

    for mutate, message in (
        (lambda item: item.pop("show_key"), "unexpected fields"),
        (lambda item: item.__setitem__("kind", 3), "kind/state must be strings"),
        (lambda item: item.__setitem__("kind", "garbage"), "kind/state is invalid"),
        (
            lambda item: item.__setitem__("collision_class", 3),
            "collision_class must be a string",
        ),
        (
            lambda item: item.__setitem__("collision_class", "garbage"),
            "collision_class is invalid",
        ),
        (lambda item: item.__setitem__("data", []), "data must be an object"),
        (
            lambda item: item.__setitem__("candidates", [1]),
            "candidates must be strings",
        ),
    ):
        payload = _session_payload()
        items = payload["items"]
        assert isinstance(items, list)
        item = items[0]
        assert isinstance(item, dict)
        mutate(item)
        with pytest.raises(ValueError, match=message):
            _load_payload(payload)


def _held_manifest() -> dict[str, object]:
    record = PlanRecord(
        source=SourceFile(
            relative_path=HELD_SOURCE,
            extension=".mkv",
            fingerprint=SourceFingerprint(size=100, mtime_ns=200, sha256="e" * 64),
        ),
        status=TerminalStatus.HELD,
        parse=ParseResult(series_hint="Fabricated Series"),
        evidence=MatchEvidence(method="source-hold-override", confidence=1.0),
        reason="fabricated reviewed hold",
    )
    return plan_to_manifest(
        OrganizerPlan(
            schema_version=PLAN_SCHEMA_VERSION,
            overrides_version=4,
            records=(record,),
        )
    )


def test_build_review_session_rejects_invalid_base_payload_and_bad_manifest_identity() -> (
    None
):
    manifest = _held_manifest()
    with pytest.raises(ValueError, match="valid UTF-8"):
        build_review_session(
            manifest,
            base_override_snapshot="b" * 64,
            base_override_payload=b"\xff",
        )

    broken = json.loads(json.dumps(manifest))
    records = broken["records"]
    assert isinstance(records, list)
    record = records[0]
    assert isinstance(record, dict)
    source = record["source"]
    assert isinstance(source, dict)
    source["fingerprint"] = {"size": True, "mtime_ns": 1, "sha256": None}
    with pytest.raises(ValueError, match="size must be an integer"):
        build_review_session(
            broken,
            base_override_snapshot="b" * 64,
            base_override_payload=b"schema_version = 4\n",
        )


def test_manifest_override_snapshot_requires_provenance_and_sha() -> None:
    manifest = _held_manifest()
    manifest.pop("provenance", None)
    with pytest.raises(ValueError, match="requires plan provenance"):
        manifest_override_snapshot(manifest)

    manifest = _held_manifest()
    manifest["provenance"] = {}
    with pytest.raises(ValueError, match="missing overrides_snapshot_id"):
        manifest_override_snapshot(manifest)

    manifest = _held_manifest()
    manifest["provenance"] = {"overrides_snapshot_id": "short"}
    with pytest.raises(ValueError, match="64 hex characters"):
        manifest_override_snapshot(manifest)
