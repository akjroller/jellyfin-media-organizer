from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from io import StringIO

import pytest

from jellyfin_show_organizer.models import (
    DuplicateCollisionClass,
    DuplicateDecision,
    OrganizerPlan,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.planner import PlanningConfigurationError
from jellyfin_show_organizer.review_contract import (
    DuplicateGroupAction,
    DuplicateGroupDecision,
    ReviewContractCatalog,
)
from jellyfin_show_organizer.review_execution import (
    _apply_duplicate_group_contract,
    _bindings_for_group,
)
from jellyfin_show_organizer.review_identity import (
    duplicate_candidate_set_hash,
    normalize_review_path,
    stable_duplicate_ref,
)
from jellyfin_show_organizer.review_session import (
    REVIEW_SESSION_SCHEMA_VERSION,
    ReviewCollisionClass,
    ReviewItemKind,
    ReviewItemState,
    ReviewSession,
    ReviewSessionItem,
)
from jellyfin_show_organizer.review_system import (
    DuplicateReviewGroup,
    ReviewAnswer,
    ReviewConfigurationError,
    _answer_duplicate,
    load_review_answers,
)

pytestmark = pytest.mark.local

DESTINATION = "Fabricated Series/Season 01/Fabricated Series S01E01.mkv"
FIRST = "Fabricated Series/A.mkv"
SECOND = "Fabricated Series/B.mkv"
CANDIDATES = (FIRST, SECOND)


def _source(path: str, *, size: int = 100, sha: str = "a" * 64) -> SourceFile:
    return SourceFile(
        relative_path=path,
        extension=".mkv",
        fingerprint=SourceFingerprint(size=size, mtime_ns=200, sha256=sha),
    )


def _decision(*, winner: str | None = FIRST) -> DuplicateDecision:
    return DuplicateDecision(
        destination_key=DESTINATION,
        candidates=CANDIDATES,
        winner=winner,
        losers=(
            ()
            if winner is None
            else tuple(item for item in CANDIDATES if item != winner)
        ),
        confidence=0.9 if winner is not None else 0.5,
        evidence=("fabricated duplicate review evidence",),
        collision_class=DuplicateCollisionClass.SAME_LOGICAL_IDENTITY,
    )


def _plan(*, winner: str | None = FIRST, changed_second: bool = False) -> OrganizerPlan:
    decision = _decision(winner=winner)
    first = PlanRecord(
        source=_source(FIRST),
        status=TerminalStatus.MATCHED if winner == FIRST else TerminalStatus.DUPLICATE,
        destination=DESTINATION if winner == FIRST else None,
        duplicate=decision,
        reason=None if winner == FIRST else "non-destructive duplicate loser",
    )
    second = PlanRecord(
        source=_source(
            SECOND,
            size=101 if changed_second else 100,
            sha="b" * 64 if changed_second else "a" * 64,
        ),
        status=TerminalStatus.MATCHED
        if winner == SECOND
        else (
            TerminalStatus.SUSPICIOUS if winner is None else TerminalStatus.DUPLICATE
        ),
        destination=DESTINATION if winner == SECOND else None,
        duplicate=decision,
        reason=None if winner == SECOND else "fabricated duplicate state",
    )
    return OrganizerPlan(schema_version=3, overrides_version=5, records=(first, second))


def _catalog(
    plan: OrganizerPlan, *, action: DuplicateGroupAction, winner: str | None
) -> ReviewContractCatalog:
    decision = plan.records[0].duplicate
    assert decision is not None
    ref = stable_duplicate_ref(decision.destination_key, decision.candidates)
    candidate_hash = duplicate_candidate_set_hash(
        decision.destination_key,
        _bindings_for_group(plan, decision.candidates),
    )
    reviewed = DuplicateGroupDecision(
        duplicate_ref=ref,
        candidate_set_sha256=candidate_hash,
        candidates=decision.candidates,
        action=action,
        winner=winner,
        reasons=("fabricated human duplicate decision",),
    )
    return ReviewContractCatalog(
        schema_version=5,
        shows=(),
        duplicate_group_decisions=(reviewed,),
        review_session_sha256="1" * 64,
        review_base_plan_sha256="2" * 64,
        review_base_override_snapshot="3" * 64,
    )


def test_reviewed_duplicate_select_winner_is_verified_without_rewriting_plan() -> None:
    plan = _plan(winner=FIRST)
    catalog = _catalog(plan, action=DuplicateGroupAction.SELECT_WINNER, winner=FIRST)

    assert _apply_duplicate_group_contract(plan, catalog) is plan


def test_reviewed_duplicate_keep_all_makes_every_candidate_nonmoving() -> None:
    plan = _plan(winner=FIRST)
    catalog = _catalog(plan, action=DuplicateGroupAction.KEEP_ALL, winner=None)

    updated = _apply_duplicate_group_contract(plan, catalog)

    assert updated is not plan
    assert {record.status for record in updated.records} == {TerminalStatus.DUPLICATE}
    assert all(
        record.destination is None or record.source.relative_path == FIRST
        for record in updated.records
    )
    assert all(record.duplicate is not None for record in updated.records)
    assert all(
        record.duplicate.winner is None
        for record in updated.records
        if record.duplicate
    )
    assert all("keep-all" in (record.reason or "") for record in updated.records)


def test_reviewed_duplicate_rejects_changed_candidate_fingerprint() -> None:
    reviewed_plan = _plan(winner=FIRST)
    catalog = _catalog(
        reviewed_plan,
        action=DuplicateGroupAction.SELECT_WINNER,
        winner=FIRST,
    )
    current = _plan(winner=FIRST, changed_second=True)

    with pytest.raises(
        PlanningConfigurationError,
        match="candidate fingerprints or companion set changed",
    ):
        _apply_duplicate_group_contract(current, catalog)


def test_reviewed_duplicate_rejects_disappeared_candidate_set() -> None:
    plan = _plan(winner=FIRST)
    catalog = _catalog(plan, action=DuplicateGroupAction.SELECT_WINNER, winner=FIRST)
    current = replace(plan, records=(plan.records[0],))

    with pytest.raises(PlanningConfigurationError, match="no longer exists"):
        _apply_duplicate_group_contract(current, catalog)


def test_reviewed_duplicate_rejects_different_planner_winner() -> None:
    reviewed_plan = _plan(winner=FIRST)
    catalog = _catalog(
        reviewed_plan,
        action=DuplicateGroupAction.SELECT_WINNER,
        winner=FIRST,
    )
    current = _plan(winner=SECOND)

    with pytest.raises(PlanningConfigurationError, match="different winner"):
        _apply_duplicate_group_contract(current, catalog)


def _review_session(
    *,
    collision_class: ReviewCollisionClass = ReviewCollisionClass.SAME_LOGICAL_IDENTITY,
) -> ReviewSession:
    ref = stable_duplicate_ref(DESTINATION, CANDIDATES)
    return ReviewSession(
        schema_version=REVIEW_SESSION_SCHEMA_VERSION,
        plan_sha256="a" * 64,
        base_override_snapshot="b" * 64,
        base_override_toml="schema_version = 4\n",
        items=(
            ReviewSessionItem(
                review_ref=ref,
                kind=ReviewItemKind.DUPLICATE,
                state=ReviewItemState.PENDING,
                show_key="Fabricated Series",
                duplicate_ref=ref,
                candidate_set_sha256="c" * 64,
                candidates=CANDIDATES,
                collision_class=collision_class,
            ),
        ),
    )


def _review_group(
    *,
    recommended: str | None = FIRST,
    collision_class: ReviewCollisionClass = ReviewCollisionClass.SAME_LOGICAL_IDENTITY,
) -> DuplicateReviewGroup:
    return DuplicateReviewGroup(
        destination_key=DESTINATION,
        candidates=CANDIDATES,
        recommended_winner=recommended,
        losers=(
            ()
            if recommended is None
            else tuple(item for item in CANDIDATES if item != recommended)
        ),
        evidence=("fabricated duplicate evidence",),
        collision_class=collision_class,
    )


def _manifest_record(path: str) -> dict[str, object]:
    return {
        "source": {
            "relative_path": path,
            "extension": ".mkv",
            "fingerprint": {"size": 100, "mtime_ns": 200, "sha256": "a" * 64},
        },
        "destination": DESTINATION,
        "evidence": {
            "method": "fabricated",
            "confidence": 1.0,
            "reasons": ["fabricated evidence"],
        },
    }


def _record_context() -> dict[str, Mapping[str, object]]:
    return {
        normalize_review_path(candidate): _manifest_record(candidate)
        for candidate in CANDIDATES
    }


def _companion_context() -> dict[str, tuple[Mapping[str, object], ...]]:
    return {}


def _input(*responses: str) -> Callable[[str], str]:
    iterator = iter(responses)
    return lambda _prompt: next(iterator)


@pytest.mark.parametrize(
    ("action", "winner", "expected_action"),
    [
        ("accept_recommended", None, "select_winner"),
        ("select_winner", SECOND, "select_winner"),
        ("keep_all", None, "keep_all"),
        ("quarantine_candidate", None, "quarantine_candidate"),
        ("defer", None, "defer"),
    ],
)
def test_duplicate_answer_actions_are_session_bound(
    action: str,
    winner: str | None,
    expected_action: str,
) -> None:
    session = _review_session()
    answer = ReviewAnswer(
        review_ref=session.items[0].review_ref,
        action=action,
        expected_identity_sha256=session.items[0].identity_sha256,
        winner=winner,
    )

    updated = _answer_duplicate(
        session,
        _review_group(),
        _record_context(),
        _companion_context(),
        answer=answer,
        input_fn=lambda _prompt: pytest.fail("answer-bound duplicate requested input"),
        output=StringIO(),
    )

    assert updated.items[0].action == expected_action
    if action == "defer":
        assert updated.items[0].state is ReviewItemState.DEFERRED
    else:
        assert updated.items[0].state is ReviewItemState.ANSWERED


def test_duplicate_answer_rejects_responses_and_unknown_winner() -> None:
    session = _review_session()
    with pytest.raises(ReviewConfigurationError, match="cannot contain provider"):
        _answer_duplicate(
            session,
            _review_group(),
            _record_context(),
            _companion_context(),
            answer=ReviewAnswer(
                review_ref=session.items[0].review_ref,
                action="keep_all",
                expected_identity_sha256=session.items[0].identity_sha256,
                responses=("unused",),
            ),
            input_fn=lambda _prompt: "",
            output=StringIO(),
        )

    with pytest.raises(ReviewConfigurationError, match="not one current candidate"):
        _answer_duplicate(
            session,
            _review_group(),
            _record_context(),
            _companion_context(),
            answer=ReviewAnswer(
                review_ref=session.items[0].review_ref,
                action="select_winner",
                expected_identity_sha256=session.items[0].identity_sha256,
                winner="Fabricated Series/C.mkv",
            ),
            input_fn=lambda _prompt: "",
            output=StringIO(),
        )


def test_destination_conflict_rejects_duplicate_winner_and_quarantine_actions() -> None:
    session = _review_session(collision_class=ReviewCollisionClass.DESTINATION_CONFLICT)
    group = _review_group(
        collision_class=ReviewCollisionClass.DESTINATION_CONFLICT,
    )
    for action in ("select_winner", "quarantine_candidate"):
        answer = ReviewAnswer(
            review_ref=session.items[0].review_ref,
            action=action,
            expected_identity_sha256=session.items[0].identity_sha256,
            winner=FIRST if action == "select_winner" else None,
        )
        with pytest.raises(ReviewConfigurationError, match="prohibited"):
            _answer_duplicate(
                session,
                group,
                _record_context(),
                _companion_context(),
                answer=answer,
                input_fn=lambda _prompt: "",
                output=StringIO(),
            )


def test_interactive_duplicate_candidate_labels_are_validated() -> None:
    session = _review_session()
    updated = _answer_duplicate(
        session,
        _review_group(recommended=None),
        _record_context(),
        _companion_context(),
        answer=None,
        input_fn=_input("s", "C2"),
        output=StringIO(),
    )
    assert updated.items[0].data["winner"] == SECOND

    for candidate_label, message in (("wat", "invalid"), ("C9", "out of range")):
        with pytest.raises(ReviewConfigurationError, match=message):
            _answer_duplicate(
                _review_session(),
                _review_group(recommended=None),
                _record_context(),
                _companion_context(),
                answer=None,
                input_fn=_input("s", candidate_label),
                output=StringIO(),
            )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{", "invalid review answers JSON"),
        (b"[]", "answers root must be an object"),
        (json.dumps({"schema_version": 2}).encode(), "unsupported contract"),
    ],
)
def test_review_answer_file_rejects_malformed_top_level_contracts(
    payload: bytes,
    message: str,
) -> None:
    with pytest.raises(ReviewConfigurationError, match=message):
        load_review_answers(payload)


def _answers_payload(entries: object) -> dict[str, object]:
    return {
        "schema_version": 2,
        "plan_sha256": "a" * 64,
        "base_override_snapshot": "b" * 64,
        "session_sha256": "c" * 64,
        "answers": entries,
    }


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({}, "answers must be an array"),
        (["bad"], "answer entries must be objects"),
        (
            [
                {
                    "review_ref": "r",
                    "action": "defer",
                    "expected_identity_sha256": "d" * 64,
                    "bogus": True,
                }
            ],
            "unsupported fields",
        ),
        (
            [
                {
                    "review_ref": "",
                    "action": "defer",
                    "expected_identity_sha256": "d" * 64,
                }
            ],
            "review_ref must be a string",
        ),
        (
            [{"review_ref": "r", "action": "", "expected_identity_sha256": "d" * 64}],
            "action must be a string",
        ),
        (
            [
                {
                    "review_ref": "r",
                    "action": "defer",
                    "expected_identity_sha256": "d" * 64,
                    "winner": 4,
                }
            ],
            "winner must be a string",
        ),
        (
            [
                {
                    "review_ref": "r",
                    "action": "defer",
                    "expected_identity_sha256": "d" * 64,
                    "responses": [1],
                }
            ],
            "responses must be strings",
        ),
        (
            [
                {
                    "review_ref": "r",
                    "action": "defer",
                    "expected_identity_sha256": "short",
                }
            ],
            "64 hex characters",
        ),
    ],
)
def test_review_answer_file_rejects_invalid_entries(
    entries: object, message: str
) -> None:
    with pytest.raises(ReviewConfigurationError, match=message):
        load_review_answers(json.dumps(_answers_payload(entries)).encode())


def test_review_answer_file_rejects_duplicate_refs() -> None:
    entry = {
        "review_ref": "duplicate-ref",
        "action": "defer",
        "expected_identity_sha256": "d" * 64,
    }
    with pytest.raises(ReviewConfigurationError, match="cannot repeat"):
        load_review_answers(json.dumps(_answers_payload([entry, entry])).encode())
