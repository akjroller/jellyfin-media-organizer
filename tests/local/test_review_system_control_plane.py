from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from pathlib import Path

import pytest

from jellyfin_show_organizer.duplicate_classifier import (
    DuplicateCandidate,
    classify_duplicate_candidates,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    ProviderIdentity,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.providers import (
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
)
from jellyfin_show_organizer.review_contract import (
    ReviewContractCatalog,
    load_review_contract_payload,
)
from jellyfin_show_organizer.review_session import (
    ReviewItemKind,
    ReviewItemState,
    ReviewSession,
    build_review_session,
    render_review_session,
)
from jellyfin_show_organizer.review_system import (
    ReviewAnswer,
    ReviewAnswerBundle,
    ReviewConfigurationError,
    _scoped_input,
    run_review_system,
)
from jellyfin_show_organizer.schema import PLAN_SCHEMA_VERSION, plan_to_manifest

pytestmark = pytest.mark.local

DUP_SHOW = "Fabricated Series"
HELD_SHOW = "Held Series"
FIRST = f"{DUP_SHOW}/A.mkv"
SECOND = f"{DUP_SHOW}/B.mkv"
HELD_SOURCE = f"{HELD_SHOW}/Held.mkv"
DESTINATION = f"{DUP_SHOW}/Season 01/{DUP_SHOW} S01E01.mkv"
EMPTY_BASE = b"schema_version = 4\n"
HELD_BASE = (
    b"schema_version = 4\n\n"
    b"[[source_holds]]\n"
    b'source = "Held Series/Held.mkv"\n'
    b'reasons = ["fabricated review hold"]\n'
)


class NoProvider:
    @property
    def provider_name(self) -> str:
        return "tvmaze"

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        raise AssertionError(f"unexpected provider search for {title}")

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        raise AssertionError(f"unexpected provider catalog for {show_identity.key}")


PROVIDER = NoProvider()


def _duplicate_records(*, winner: bool) -> tuple[PlanRecord, PlanRecord]:
    candidates = (
        DuplicateCandidate(
            operation_key=FIRST,
            members=(FIRST,),
            destination=DESTINATION,
            logical_identity="tvmaze:4242:episode:9001",
            fingerprint=SourceFingerprint(
                size=100,
                mtime_ns=200,
                sha256="a" * 64 if winner else None,
            ),
        ),
        DuplicateCandidate(
            operation_key=SECOND,
            members=(SECOND,),
            destination=DESTINATION,
            logical_identity="tvmaze:4242:episode:9001",
            fingerprint=SourceFingerprint(
                size=100,
                mtime_ns=201,
                sha256="a" * 64 if winner else None,
            ),
        ),
    )
    decision = classify_duplicate_candidates(candidates)[0].decision
    parse = ParseResult(series_hint=DUP_SHOW, season=1, episodes=(1,))
    show = CanonicalShow(
        source_key=DUP_SHOW,
        tvmaze_id=4242,
        title=DUP_SHOW,
        numbering_mode=NumberingMode.AIRED,
    )
    evidence = MatchEvidence(method="fabricated-control-plane", confidence=1.0)
    records: list[PlanRecord] = []
    for candidate in candidates:
        selected = decision.winner == candidate.operation_key
        status = (
            TerminalStatus.MATCHED
            if selected
            else TerminalStatus.DUPLICATE
            if decision.winner is not None
            else TerminalStatus.SUSPICIOUS
        )
        records.append(
            PlanRecord(
                source=SourceFile(
                    relative_path=candidate.operation_key,
                    extension=".mkv",
                    fingerprint=candidate.fingerprint,
                ),
                status=status,
                parse=parse if selected else None,
                show=show if selected else None,
                evidence=evidence if selected else None,
                destination=decision.destination_key if selected else None,
                duplicate=decision,
                reason=None if selected else "fabricated duplicate review",
            )
        )
    return records[0], records[1]


def _held_record() -> PlanRecord:
    return PlanRecord(
        source=SourceFile(
            relative_path=HELD_SOURCE,
            extension=".mkv",
            fingerprint=SourceFingerprint(
                size=123,
                mtime_ns=456,
                sha256="b" * 64,
            ),
        ),
        status=TerminalStatus.HELD,
        parse=ParseResult(series_hint=HELD_SHOW),
        evidence=MatchEvidence(method="source-hold-override", confidence=1.0),
        reason="fabricated review hold",
    )


def _manifest(
    *,
    duplicate_winner: bool | None = True,
    include_held: bool = False,
) -> dict[str, object]:
    records: list[PlanRecord] = []
    if duplicate_winner is not None:
        records.extend(_duplicate_records(winner=duplicate_winner))
    if include_held:
        records.append(_held_record())
    return plan_to_manifest(
        OrganizerPlan(
            schema_version=PLAN_SCHEMA_VERSION,
            overrides_version=4,
            records=tuple(records),
        )
    )


def _base(*, held: bool = False) -> tuple[bytes, str]:
    payload = HELD_BASE if held else EMPTY_BASE
    snapshot = load_review_contract_payload(payload).snapshot_id
    return payload, snapshot


def _paths(tmp_path: Path, prefix: str) -> tuple[Path, Path]:
    return tmp_path / f"{prefix}-session.json", tmp_path / f"{prefix}-active.toml"


def _input(*responses: str) -> Callable[[str], str]:
    iterator = iter(responses)
    return lambda _prompt: next(iterator)


def _starting_session(
    manifest: dict[str, object],
    base_payload: bytes,
    base_snapshot: str,
) -> ReviewSession:
    return build_review_session(
        manifest,
        base_override_snapshot=base_snapshot,
        base_override_payload=base_payload,
    )


def _answer_bundle(
    session: ReviewSession,
    answers: tuple[ReviewAnswer, ...],
    *,
    plan_sha256: str | None = None,
    base_snapshot: str | None = None,
    session_sha256: str | None = None,
) -> ReviewAnswerBundle:
    return ReviewAnswerBundle(
        plan_sha256=plan_sha256 or session.plan_sha256,
        base_override_snapshot=base_snapshot or session.base_override_snapshot,
        session_sha256=session_sha256 or session.sha256,
        answers=answers,
    )


def test_batch_accept_recommended_completes_duplicate_review(tmp_path: Path) -> None:
    manifest = _manifest()
    base_payload, base_snapshot = _base()
    session_path, active_path = _paths(tmp_path, "batch")
    output = StringIO()

    session, active = run_review_system(
        manifest,
        base_payload,
        base_override_snapshot=base_snapshot,
        provider=PROVIDER,
        session_path=session_path,
        output_override_path=active_path,
        resume=False,
        input_fn=_input("yes"),
        output=output,
        batch_accept_recommended=True,
    )

    assert session.complete
    assert len(session.items) == 1
    assert session.items[0].kind is ReviewItemKind.DUPLICATE
    assert session.items[0].action == "select_winner"
    assert "Batch recommended winners:" in output.getvalue()
    catalog = load_review_contract_payload(active)
    assert isinstance(catalog, ReviewContractCatalog)
    assert len(catalog.duplicate_group_decisions) == 1
    assert active_path.read_bytes() == active


def test_batch_rejection_falls_through_to_interactive_defer(tmp_path: Path) -> None:
    manifest = _manifest()
    base_payload, base_snapshot = _base()
    session_path, active_path = _paths(tmp_path, "batch-defer")

    session, _ = run_review_system(
        manifest,
        base_payload,
        base_override_snapshot=base_snapshot,
        provider=PROVIDER,
        session_path=session_path,
        output_override_path=active_path,
        resume=False,
        input_fn=_input("n", "d"),
        output=StringIO(),
        batch_accept_recommended=True,
    )

    assert not session.complete
    assert session.items[0].state is ReviewItemState.DEFERRED
    assert session.items[0].action == "defer"


def test_batch_accept_requires_recommended_winner(tmp_path: Path) -> None:
    manifest = _manifest(duplicate_winner=False)
    base_payload, base_snapshot = _base()
    session_path, active_path = _paths(tmp_path, "winnerless")

    with pytest.raises(
        ReviewConfigurationError,
        match="requires a winner for every selected group",
    ):
        run_review_system(
            manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=session_path,
            output_override_path=active_path,
            resume=False,
            input_fn=lambda _prompt: pytest.fail("winnerless batch prompted"),
            output=StringIO(),
            batch_accept_recommended=True,
        )


def test_interactive_held_keep_and_defer_paths(tmp_path: Path) -> None:
    manifest = _manifest(duplicate_winner=None, include_held=True)
    base_payload, base_snapshot = _base(held=True)

    for prefix, response, expected_state, expected_action in (
        ("held-keep", "h", ReviewItemState.ANSWERED, "keep_held"),
        ("held-defer", "d", ReviewItemState.DEFERRED, "defer"),
    ):
        session_path, active_path = _paths(tmp_path, prefix)
        session, _ = run_review_system(
            manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=session_path,
            output_override_path=active_path,
            resume=False,
            input_fn=_input(response),
            output=StringIO(),
        )
        assert session.items[0].state is expected_state
        assert session.items[0].action == expected_action


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("plan", "answers are not bound to this exact plan"),
        ("base", "answers are not bound to this base override snapshot"),
        ("session", "answers are not bound to this exact starting review session"),
    ],
)
def test_answers_must_match_exact_starting_contract(
    tmp_path: Path,
    field: str,
    message: str,
) -> None:
    manifest = _manifest()
    base_payload, base_snapshot = _base()
    starting = _starting_session(manifest, base_payload, base_snapshot)
    item = starting.items[0]
    answer = ReviewAnswer(
        review_ref=item.review_ref,
        action="accept_recommended",
        expected_identity_sha256=item.identity_sha256,
    )
    bundle = _answer_bundle(
        starting,
        (answer,),
        plan_sha256="f" * 64 if field == "plan" else None,
        base_snapshot="e" * 64 if field == "base" else None,
        session_sha256="d" * 64 if field == "session" else None,
    )
    session_path, active_path = _paths(tmp_path, f"answers-{field}")

    with pytest.raises(ReviewConfigurationError, match=message):
        run_review_system(
            manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=session_path,
            output_override_path=active_path,
            resume=False,
            input_fn=lambda _prompt: pytest.fail("bound answers requested input"),
            output=StringIO(),
            answers=bundle,
        )


def test_answers_must_match_selected_scope_and_cover_unresolved_items(
    tmp_path: Path,
) -> None:
    manifest = _manifest(include_held=True)
    base_payload, base_snapshot = _base(held=True)
    starting = _starting_session(manifest, base_payload, base_snapshot)
    duplicate = next(
        item for item in starting.items if item.kind is ReviewItemKind.DUPLICATE
    )
    held = next(item for item in starting.items if item.kind is ReviewItemKind.HELD)

    held_answer = ReviewAnswer(
        review_ref=held.review_ref,
        action="keep_held",
        expected_identity_sha256=held.identity_sha256,
    )
    outside = _answer_bundle(starting, (held_answer,))
    session_path, active_path = _paths(tmp_path, "outside-scope")
    with pytest.raises(
        ReviewConfigurationError, match="outside the selected review scope"
    ):
        run_review_system(
            manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=session_path,
            output_override_path=active_path,
            resume=False,
            input_fn=lambda _prompt: pytest.fail("outside-scope answers prompted"),
            output=StringIO(),
            kind_filter="duplicate",
            answers=outside,
        )

    missing = _answer_bundle(starting, ())
    session_path, active_path = _paths(tmp_path, "missing-scope")
    with pytest.raises(ReviewConfigurationError, match="do not cover every unresolved"):
        run_review_system(
            manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=session_path,
            output_override_path=active_path,
            resume=False,
            input_fn=lambda _prompt: pytest.fail("missing answers prompted"),
            output=StringIO(),
            kind_filter="duplicate",
            answers=missing,
        )

    duplicate_answer = ReviewAnswer(
        review_ref=duplicate.review_ref,
        action="accept_recommended",
        expected_identity_sha256=duplicate.identity_sha256,
    )
    exact = _answer_bundle(starting, (duplicate_answer,))
    session_path, active_path = _paths(tmp_path, "batch-answers")
    with pytest.raises(
        ReviewConfigurationError, match="cannot be combined with --answers"
    ):
        run_review_system(
            manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=session_path,
            output_override_path=active_path,
            resume=False,
            input_fn=lambda _prompt: pytest.fail("batch answers prompted"),
            output=StringIO(),
            kind_filter="duplicate",
            batch_accept_recommended=True,
            answers=exact,
        )


def test_partial_approval_requires_explicit_nonempty_answered_scope(
    tmp_path: Path,
) -> None:
    held_manifest = _manifest(duplicate_winner=None, include_held=True)
    base_payload, base_snapshot = _base(held=True)
    session_path, active_path = _paths(tmp_path, "partial-unscoped")
    with pytest.raises(ReviewConfigurationError, match="requires an explicit"):
        run_review_system(
            held_manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=session_path,
            output_override_path=active_path,
            resume=False,
            input_fn=_input("d"),
            output=StringIO(),
            approve_partial=True,
        )

    session_path, active_path = _paths(tmp_path, "partial-empty")
    with pytest.raises(ReviewConfigurationError, match="selected no review items"):
        run_review_system(
            held_manifest,
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=session_path,
            output_override_path=active_path,
            resume=False,
            input_fn=lambda _prompt: pytest.fail("empty partial scope prompted"),
            output=StringIO(),
            show_filter="No Such Show",
            approve_partial=True,
        )


def test_valid_partial_approval_binds_only_answered_duplicate_scope(
    tmp_path: Path,
) -> None:
    manifest = _manifest(include_held=True)
    base_payload, base_snapshot = _base(held=True)
    session_path, active_path = _paths(tmp_path, "partial-valid")

    session, active = run_review_system(
        manifest,
        base_payload,
        base_override_snapshot=base_snapshot,
        provider=PROVIDER,
        session_path=session_path,
        output_override_path=active_path,
        resume=False,
        input_fn=_input("y"),
        output=StringIO(),
        kind_filter="duplicate",
        batch_accept_recommended=True,
        approve_partial=True,
    )

    duplicate = next(
        item for item in session.items if item.kind is ReviewItemKind.DUPLICATE
    )
    held = next(item for item in session.items if item.kind is ReviewItemKind.HELD)
    assert duplicate.state is ReviewItemState.ANSWERED
    assert held.state is ReviewItemState.PENDING
    assert session.approved_partial
    assert session.approved_scope_refs == (duplicate.review_ref,)
    assert session.usable_for_planning
    assert isinstance(load_review_contract_payload(active), ReviewContractCatalog)


def test_resume_rejects_changed_plan_or_base_snapshot(tmp_path: Path) -> None:
    manifest = _manifest()
    base_payload, base_snapshot = _base()
    starting = _starting_session(manifest, base_payload, base_snapshot)

    plan_session, plan_active = _paths(tmp_path, "resume-plan")
    plan_session.write_bytes(render_review_session(starting))
    with pytest.raises(ReviewConfigurationError, match="resume plan hash"):
        run_review_system(
            _manifest(duplicate_winner=False),
            base_payload,
            base_override_snapshot=base_snapshot,
            provider=PROVIDER,
            session_path=plan_session,
            output_override_path=plan_active,
            resume=True,
            input_fn=lambda _prompt: pytest.fail("resume mismatch prompted"),
            output=StringIO(),
        )

    base_session, base_active = _paths(tmp_path, "resume-base")
    base_session.write_bytes(render_review_session(starting))
    with pytest.raises(ReviewConfigurationError, match="resume base override snapshot"):
        run_review_system(
            manifest,
            base_payload,
            base_override_snapshot="f" * 64,
            provider=PROVIDER,
            session_path=base_session,
            output_override_path=base_active,
            resume=True,
            input_fn=lambda _prompt: pytest.fail("resume base mismatch prompted"),
            output=StringIO(),
        )


def test_scoped_input_rejects_unused_provider_responses() -> None:
    read, finish = _scoped_input(("used", "unused"))
    assert read("prompt") == "used"
    with pytest.raises(
        ReviewConfigurationError, match="unused provider prompt responses"
    ):
        finish()
