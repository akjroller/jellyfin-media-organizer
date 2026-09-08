from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import (
    MatchEvidence,
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
    ReviewItemState,
    build_review_session,
)
from jellyfin_show_organizer.review_system import (
    ReviewAnswer,
    ReviewAnswerBundle,
    ReviewConfigurationError,
    run_review_system,
)
from jellyfin_show_organizer.schema import PLAN_SCHEMA_VERSION, plan_to_manifest

pytestmark = pytest.mark.local

EMPTY_BASE = b"schema_version = 4\n"
SHOW_A = "Synthetic Ambiguous A"
SHOW_B = "Synthetic Ambiguous B"
SOURCE_A = f"{SHOW_A}/ambiguous-a.mkv"
SOURCE_B = f"{SHOW_B}/ambiguous-b.mkv"


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


def _record(
    source: str,
    show: str,
    status: TerminalStatus,
    *,
    marker: str,
) -> PlanRecord:
    return PlanRecord(
        source=SourceFile(
            relative_path=source,
            extension=".mkv",
            fingerprint=SourceFingerprint(
                size=1_000 + ord(marker),
                mtime_ns=2_000 + ord(marker),
                sha256=marker * 64,
            ),
        ),
        status=status,
        parse=ParseResult(series_hint=show, season=1, episodes=(1, 2)),
        evidence=MatchEvidence(
            method="synthetic-ambiguous-review",
            confidence=0.0,
            reasons=(f"synthetic ambiguity {marker}",),
        ),
        reason=f"synthetic unresolved source {marker}",
    )


def _manifest() -> dict[str, object]:
    plan = OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=4,
        records=(
            _record(SOURCE_A, SHOW_A, TerminalStatus.SUSPICIOUS, marker="a"),
            _record(SOURCE_B, SHOW_B, TerminalStatus.UNRESOLVED, marker="b"),
        ),
    )
    return plan_to_manifest(plan)


def _base_snapshot() -> str:
    return load_review_contract_payload(EMPTY_BASE).snapshot_id


def _input(*responses: str):
    iterator = iter(responses)
    return lambda _prompt: next(iterator)


def test_batch_keep_held_marks_selected_ambiguous_sources_untouched(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.json"
    active_path = tmp_path / "active.toml"
    output = StringIO()

    session, active = run_review_system(
        _manifest(),
        EMPTY_BASE,
        base_override_snapshot=_base_snapshot(),
        provider=PROVIDER,
        session_path=session_path,
        output_override_path=active_path,
        resume=False,
        input_fn=_input("yes"),
        output=output,
        kind_filter="held",
        batch_keep_held=True,
    )

    assert session.complete
    assert all(item.state is ReviewItemState.ANSWERED for item in session.items)
    assert all(item.action == "keep_held" for item in session.items)
    catalog = load_review_contract_payload(active)
    assert isinstance(catalog, ReviewContractCatalog)
    assert {hold.source for hold in catalog.source_holds} == {SOURCE_A, SOURCE_B}
    rendered = output.getvalue()
    assert "Batch leave-untouched sources:" in rendered
    assert SOURCE_A in rendered
    assert SOURCE_B in rendered
    assert "[suspicious]" in rendered
    assert "[unresolved]" in rendered


def test_batch_keep_held_rejection_falls_through_to_interactive_review(
    tmp_path: Path,
) -> None:
    session, _ = run_review_system(
        _manifest(),
        EMPTY_BASE,
        base_override_snapshot=_base_snapshot(),
        provider=PROVIDER,
        session_path=tmp_path / "session.json",
        output_override_path=tmp_path / "active.toml",
        resume=False,
        input_fn=_input("no", "d", "d"),
        output=StringIO(),
        kind_filter="held",
        batch_keep_held=True,
    )

    assert not session.complete
    assert all(item.state is ReviewItemState.DEFERRED for item in session.items)
    assert all(item.action == "defer" for item in session.items)


def test_batch_keep_held_respects_explicit_show_scope(tmp_path: Path) -> None:
    session, active = run_review_system(
        _manifest(),
        EMPTY_BASE,
        base_override_snapshot=_base_snapshot(),
        provider=PROVIDER,
        session_path=tmp_path / "session.json",
        output_override_path=tmp_path / "active.toml",
        resume=False,
        input_fn=_input("yes"),
        output=StringIO(),
        show_filter=SHOW_A,
        batch_keep_held=True,
    )

    by_show = {item.show_key: item for item in session.items}
    assert by_show[SHOW_A].state is ReviewItemState.ANSWERED
    assert by_show[SHOW_A].action == "keep_held"
    assert by_show[SHOW_B].state is ReviewItemState.PENDING
    catalog = load_review_contract_payload(active)
    assert isinstance(catalog, ReviewContractCatalog)
    assert [hold.source for hold in catalog.source_holds] == [SOURCE_A]


def test_batch_keep_held_cannot_be_combined_with_answers(tmp_path: Path) -> None:
    manifest = _manifest()
    starting = build_review_session(
        manifest,
        base_override_snapshot=_base_snapshot(),
        base_override_payload=EMPTY_BASE,
    )
    answers = ReviewAnswerBundle(
        plan_sha256=starting.plan_sha256,
        base_override_snapshot=starting.base_override_snapshot,
        session_sha256=starting.sha256,
        answers=tuple(
            ReviewAnswer(
                review_ref=item.review_ref,
                action="keep_held",
                expected_identity_sha256=item.identity_sha256,
            )
            for item in starting.items
        ),
    )

    with pytest.raises(
        ReviewConfigurationError,
        match="--batch-keep-held cannot be combined with --answers",
    ):
        run_review_system(
            manifest,
            EMPTY_BASE,
            base_override_snapshot=_base_snapshot(),
            provider=PROVIDER,
            session_path=tmp_path / "session.json",
            output_override_path=tmp_path / "active.toml",
            resume=False,
            input_fn=lambda _prompt: pytest.fail("batch/answers conflict prompted"),
            output=StringIO(),
            kind_filter="held",
            batch_keep_held=True,
            answers=answers,
        )
