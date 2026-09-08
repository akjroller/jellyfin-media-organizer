import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import jellyfin_show_organizer.apply_execution as apply_execution
from jellyfin_show_organizer.apply_contract import (
    ApplyApproval,
    ApplyContractError,
    ApplyJournalEntry,
    ApplyMemberRole,
    ApplyReviewApproval,
    ApplyReviewMode,
    JournalEvent,
    build_apply_contract,
    derive_apply_group_ids,
    manifest_plan_hash,
    replay_journal,
)
from jellyfin_show_organizer.apply_execution import ApplyExecutionError, prepare_apply
from jellyfin_show_organizer.models import (
    CacheSnapshot,
    CanonicalShow,
    CompanionPlanRecord,
    CompanionStatus,
    DuplicateCollisionClass,
    DuplicateDecision,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanEpisode,
    PlanProvenance,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.schema import (
    PLAN_SCHEMA_VERSION,
    plan_to_manifest,
    stable_plan_hash,
)

pytestmark = pytest.mark.local


def _cache_snapshot() -> CacheSnapshot:
    return CacheSnapshot(
        provider="tvmaze",
        kind="episode-catalog",
        request_key="45001",
        snapshot_id="d" * 64,
        state="ok",
    )


def _plan(
    *,
    video_status: TerminalStatus = TerminalStatus.MATCHED,
    overrides_version: int = 2,
) -> OrganizerPlan:
    source = SourceFile(
        relative_path="Example Series/release-a.mkv",
        extension=".mkv",
        fingerprint=SourceFingerprint(size=100, mtime_ns=10, sha256="1" * 64),
    )
    record = PlanRecord(
        source=source,
        status=video_status,
        parse=ParseResult(series_hint="Example Series", season=1, episodes=(1,)),
        show=CanonicalShow(
            source_key="Example Series",
            tvmaze_id=45001,
            title="Example Series",
            year=2024,
            numbering_mode=NumberingMode.AIRED,
        ),
        evidence=MatchEvidence(method="synthetic", confidence=1.0),
        destination=(
            "Example Series (2024)/Season 01/Example Series (2024) S01E01 - Pilot.mkv"
        ),
        operation_group_id="op-example",
        provider_episodes=(
            PlanEpisode(
                tvmaze_episode_id=90001,
                season=1,
                number=1,
                title="Pilot",
            ),
        ),
        reason="review required" if video_status is TerminalStatus.UNRESOLVED else None,
    )
    companion = CompanionPlanRecord(
        relative_path="Example Series/release-a.en.srt",
        extension=".srt",
        fingerprint=SourceFingerprint(size=5, mtime_ns=11, sha256="2" * 64),
        status=CompanionStatus.ASSOCIATED,
        reason="subtitle-associated",
        source_video=source.relative_path,
        operation_group_id="op-example",
        destination=(
            "Example Series (2024)/Season 01/"
            "Example Series (2024) S01E01 - Pilot.en.srt"
        ),
        kind="subtitle",
    )
    return OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=overrides_version,
        records=(record,),
        companions=(companion,),
        provenance=PlanProvenance(
            tool_version="0.1.0",
            config_snapshot_id="a" * 64,
            overrides_snapshot_id="b" * 64,
            cache_snapshots=(_cache_snapshot(),),
        ),
    )


def _approval(
    plan: OrganizerPlan,
    *,
    review: ApplyReviewApproval | None = None,
    authorized_group_ids: tuple[str, ...] = ("op-example",),
) -> ApplyApproval:
    assert plan.provenance is not None
    return ApplyApproval(
        plan_sha256=stable_plan_hash(plan),
        schema_version=plan.schema_version,
        tool_version=plan.provenance.tool_version,
        config_snapshot_id=plan.provenance.config_snapshot_id,
        overrides_snapshot_id=plan.provenance.overrides_snapshot_id,
        cache_snapshots=plan.provenance.cache_snapshots,
        authorized_group_ids=authorized_group_ids,
        review=review,
    )


def _preflight(plan: OrganizerPlan, *, ready: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "plan_hash": stable_plan_hash(plan),
        "ready": ready,
        "blocked_group_ids": [] if ready else ["op-example"],
        "findings": []
        if ready
        else [
            {
                "code": "synthetic-block",
                "record_ids": ["video:Example Series/release-a.mkv"],
                "group_ids": ["op-example"],
            }
        ],
    }


def _review_provenance(
    plan: OrganizerPlan,
    *,
    scope_state: ApplyReviewMode,
    approved_scope_refs: tuple[str, ...] = (),
    session_sha256: str = "c" * 64,
) -> dict[str, object]:
    return {
        "plan_sha256": stable_plan_hash(plan),
        "review": {
            "session_sha256": session_sha256,
            "base_plan_sha256": "e" * 64,
            "base_override_snapshot": "f" * 64,
            "scope_state": scope_state.value,
            "approved_scope_refs": list(approved_scope_refs),
            "authorization": "review-state-only",
            "movement_authorized": False,
            "full_plan_approval_required": True,
        },
    }


def test_approved_ready_plan_derives_one_indivisible_operation_group():
    plan = _plan()
    contract = build_apply_contract(
        plan_to_manifest(plan),
        _preflight(plan),
        _approval(plan),
    )

    assert contract.plan_sha256 == stable_plan_hash(plan)
    assert len(contract.groups) == 1
    group = contract.groups[0]
    assert group.group_id == "op-example"
    assert [member.role for member in group.members] == [
        ApplyMemberRole.VIDEO,
        ApplyMemberRole.COMPANION,
    ]
    assert len(group.moving_members) == 2


def test_duplicate_loser_with_destination_never_enters_apply_scope():
    plan = _plan()
    winner = plan.records[0]
    loser_source = replace(
        winner.source,
        relative_path="Example Series/release-b.mkv",
    )
    candidates = (winner.source.relative_path, loser_source.relative_path)
    decision = DuplicateDecision(
        destination_key=winner.destination or "",
        candidates=candidates,
        winner=winner.source.relative_path,
        losers=(loser_source.relative_path,),
        confidence=1.0,
        evidence=("synthetic reviewed winner",),
        collision_class=DuplicateCollisionClass.SAME_LOGICAL_IDENTITY,
    )
    winner = replace(winner, duplicate=decision)
    loser = replace(
        winner,
        source=loser_source,
        status=TerminalStatus.DUPLICATE,
        duplicate=decision,
        operation_group_id="op-loser",
        reason="duplicate loser",
    )
    changed = replace(plan, records=(winner, loser))

    assert derive_apply_group_ids(plan_to_manifest(changed)) == ("op-example",)


def test_prepare_apply_binds_clean_revision_and_complete_review(tmp_path: Path):
    plan = _plan(overrides_version=5)
    manifest = plan_to_manifest(plan)
    provenance = _review_provenance(plan, scope_state=ApplyReviewMode.COMPLETE)
    provenance.update(
        {
            "source_revision": {
                "state": "git",
                "commit": "9" * 40,
                "dirty": False,
            },
            "provider": {"failure": False},
            "preflight": {"ready": True, "finding_count": 0},
        }
    )
    plan_path = tmp_path / "plan.json"
    preflight_path = tmp_path / "preflight.json"
    provenance_path = tmp_path / "run-provenance.json"
    plan_path.write_text(json.dumps(manifest), encoding="utf-8")
    preflight_path.write_text(json.dumps(_preflight(plan)), encoding="utf-8")
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    prepared = prepare_apply(
        plan_path,
        preflight_path,
        provenance_path,
        approved_plan_sha256=stable_plan_hash(plan),
        approved_review_session_sha256="c" * 64,
        approved_source_revision="9" * 40,
    )

    assert prepared.contract.plan_sha256 == stable_plan_hash(plan)
    assert [group.group_id for group in prepared.contract.groups] == ["op-example"]

    provenance["source_revision"] = {
        "state": "git",
        "commit": "9" * 40,
        "dirty": True,
    }
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(ApplyExecutionError, match="clean revision"):
        prepare_apply(
            plan_path,
            preflight_path,
            provenance_path,
            approved_plan_sha256=stable_plan_hash(plan),
            approved_review_session_sha256="c" * 64,
            approved_source_revision="9" * 40,
        )


def test_prepare_apply_rejects_each_changed_approval_artifact(tmp_path: Path):
    plan = _plan(overrides_version=5)
    manifest = plan_to_manifest(plan)
    plan_hash = stable_plan_hash(plan)
    provenance = _review_provenance(plan, scope_state=ApplyReviewMode.COMPLETE)
    provenance.update(
        {
            "source_revision": {
                "state": "git",
                "commit": "9" * 40,
                "dirty": False,
            },
            "provider": {"failure": False},
            "preflight": {"ready": True, "finding_count": 0},
        }
    )
    plan_path = tmp_path / "plan.json"
    preflight_path = tmp_path / "preflight.json"
    provenance_path = tmp_path / "run-provenance.json"
    plan_path.write_text(json.dumps(manifest), encoding="utf-8")
    preflight_path.write_text(json.dumps(_preflight(plan)), encoding="utf-8")

    def attempt(
        changed: dict[str, object],
        *,
        approved_plan: str = plan_hash,
        approved_session: str = "c" * 64,
        approved_revision: str = "9" * 40,
    ) -> None:
        provenance_path.write_text(json.dumps(changed), encoding="utf-8")
        prepare_apply(
            plan_path,
            preflight_path,
            provenance_path,
            approved_plan_sha256=approved_plan,
            approved_review_session_sha256=approved_session,
            approved_source_revision=approved_revision,
        )

    with pytest.raises(ApplyExecutionError, match="invalid format"):
        attempt(provenance, approved_plan="short")
    with pytest.raises(ApplyExecutionError, match="does not match plan.json"):
        attempt(provenance, approved_plan="0" * 64)

    changed = copy.deepcopy(provenance)
    changed["plan_sha256"] = "0" * 64
    with pytest.raises(ApplyExecutionError, match="provenance does not match"):
        attempt(changed)

    changed = copy.deepcopy(provenance)
    changed["preflight"] = {"ready": False, "finding_count": 1}
    with pytest.raises(ApplyExecutionError, match="ready plan"):
        attempt(changed)

    changed = copy.deepcopy(provenance)
    changed["provider"] = {"failure": True}
    with pytest.raises(ApplyExecutionError, match="provider failure"):
        attempt(changed)

    changed = copy.deepcopy(provenance)
    review = cast(dict[str, object], changed["review"])
    review["session_sha256"] = "0" * 64
    with pytest.raises(ApplyExecutionError, match="review-session"):
        attempt(changed)

    changed = copy.deepcopy(provenance)
    review = cast(dict[str, object], changed["review"])
    review["scope_state"] = ApplyReviewMode.APPROVED_PARTIAL.value
    with pytest.raises(ApplyExecutionError, match="complete review"):
        attempt(changed)

    changed = copy.deepcopy(provenance)
    review = cast(dict[str, object], changed["review"])
    review["approved_scope_refs"] = ["held-0123456789abcdef"]
    with pytest.raises(ApplyExecutionError, match="partial review scope"):
        attempt(changed)


def test_prepare_apply_rejects_malformed_and_unbuildable_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    plan = _plan(overrides_version=5)
    manifest = plan_to_manifest(plan)
    provenance = _review_provenance(plan, scope_state=ApplyReviewMode.COMPLETE)
    provenance.update(
        {
            "source_revision": {
                "state": "git",
                "commit": "9" * 40,
                "dirty": False,
            },
            "provider": {"failure": False},
            "preflight": {"ready": True, "finding_count": 0},
        }
    )
    plan_path = tmp_path / "plan.json"
    preflight_path = tmp_path / "preflight.json"
    provenance_path = tmp_path / "run-provenance.json"

    def write_artifacts(changed_manifest: object) -> str:
        changed_hash = manifest_plan_hash(changed_manifest)
        changed_preflight = _preflight(plan)
        changed_preflight["plan_hash"] = changed_hash
        changed_provenance = copy.deepcopy(provenance)
        changed_provenance["plan_sha256"] = changed_hash
        plan_path.write_text(json.dumps(changed_manifest), encoding="utf-8")
        preflight_path.write_text(json.dumps(changed_preflight), encoding="utf-8")
        provenance_path.write_text(json.dumps(changed_provenance), encoding="utf-8")
        return changed_hash

    plan_path.write_text("not JSON", encoding="utf-8")
    preflight_path.write_text("{}", encoding="utf-8")
    provenance_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ApplyExecutionError, match="valid plan manifest"):
        prepare_apply(
            plan_path,
            preflight_path,
            provenance_path,
            approved_plan_sha256="a" * 64,
            approved_review_session_sha256="c" * 64,
            approved_source_revision="9" * 40,
        )

    changed_hash = write_artifacts(manifest)
    monkeypatch.setattr(
        apply_execution,
        "build_apply_contract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ApplyContractError("synthetic contract refusal")
        ),
    )
    with pytest.raises(ApplyExecutionError, match="synthetic contract refusal"):
        prepare_apply(
            plan_path,
            preflight_path,
            provenance_path,
            approved_plan_sha256=changed_hash,
            approved_review_session_sha256="c" * 64,
            approved_source_revision="9" * 40,
        )


def test_hash_snapshot_and_preflight_mismatches_fail_closed():
    plan = _plan()
    manifest = plan_to_manifest(plan)

    wrong_hash = replace(_approval(plan), plan_sha256="0" * 64)
    with pytest.raises(ApplyContractError, match="approved plan hash"):
        build_apply_contract(manifest, _preflight(plan), wrong_hash)

    wrong_config = replace(_approval(plan), config_snapshot_id="c" * 64)
    with pytest.raises(ApplyContractError, match="config snapshot"):
        build_apply_contract(manifest, _preflight(plan), wrong_config)

    with pytest.raises(ApplyContractError, match="preflight is not ready"):
        build_apply_contract(manifest, _preflight(plan, ready=False), _approval(plan))


def test_approval_binds_exact_derived_operation_group_scope():
    plan = _plan()

    with pytest.raises(ApplyContractError, match="operation-group scope"):
        build_apply_contract(
            plan_to_manifest(plan),
            _preflight(plan),
            _approval(plan, authorized_group_ids=()),
        )


def test_unresolved_video_cannot_cross_apply_boundary():
    plan = _plan(video_status=TerminalStatus.UNRESOLVED)

    with pytest.raises(ApplyContractError, match="unresolved or suspicious"):
        build_apply_contract(
            plan_to_manifest(plan),
            _preflight(plan),
            _approval(plan),
        )


def test_associated_companion_must_share_its_video_operation_group():
    plan = _plan()
    companion = replace(plan.companions[0], operation_group_id="op-other")
    changed = replace(plan, companions=(companion,))

    with pytest.raises(ApplyContractError, match="does not match its video"):
        build_apply_contract(
            plan_to_manifest(changed),
            _preflight(changed),
            _approval(changed),
        )


def test_schema5_reviewed_plan_rejects_generic_full_plan_approval():
    plan = _plan(overrides_version=5)
    provenance = _review_provenance(plan, scope_state=ApplyReviewMode.COMPLETE)

    with pytest.raises(ApplyContractError, match="exact review session"):
        build_apply_contract(
            plan_to_manifest(plan),
            _preflight(plan),
            _approval(plan),
            run_provenance=provenance,
        )


def test_partial_review_provenance_can_never_authorize_apply():
    plan = _plan(overrides_version=5)
    refs = ("held-0123456789abcdef",)
    review = ApplyReviewApproval(
        session_sha256="c" * 64,
        mode=ApplyReviewMode.APPROVED_PARTIAL,
        approved_scope_refs=refs,
    )
    provenance = _review_provenance(
        plan,
        scope_state=ApplyReviewMode.APPROVED_PARTIAL,
        approved_scope_refs=refs,
    )

    with pytest.raises(ApplyContractError, match="partial-review provenance"):
        build_apply_contract(
            plan_to_manifest(plan),
            _preflight(plan),
            _approval(plan, review=review),
            run_provenance=provenance,
        )


def test_complete_review_apply_approval_binds_exact_session_and_scope():
    plan = _plan(overrides_version=5)
    review = ApplyReviewApproval(
        session_sha256="c" * 64,
        mode=ApplyReviewMode.COMPLETE,
        approved_scope_refs=(),
    )
    provenance = _review_provenance(plan, scope_state=ApplyReviewMode.COMPLETE)

    contract = build_apply_contract(
        plan_to_manifest(plan),
        _preflight(plan),
        _approval(plan, review=review),
        run_provenance=provenance,
    )

    assert [group.group_id for group in contract.groups] == ["op-example"]

    wrong_session = replace(review, session_sha256="9" * 64)
    with pytest.raises(ApplyContractError, match="session hash"):
        build_apply_contract(
            plan_to_manifest(plan),
            _preflight(plan),
            _approval(plan, review=wrong_session),
            run_provenance=provenance,
        )


def test_schema5_reviewed_plan_requires_run_provenance():
    plan = _plan(overrides_version=5)
    review = ApplyReviewApproval(
        session_sha256="c" * 64,
        mode=ApplyReviewMode.COMPLETE,
    )

    with pytest.raises(ApplyContractError, match="review run provenance"):
        build_apply_contract(
            plan_to_manifest(plan),
            _preflight(plan),
            _approval(plan, review=review),
        )


def test_journal_replay_preserves_completed_members_across_failed_resume():
    plan = _plan()
    contract = build_apply_contract(
        plan_to_manifest(plan),
        _preflight(plan),
        _approval(plan),
    )
    group = contract.groups[0]
    video, subtitle = group.moving_members

    incomplete = replay_journal(
        contract,
        (
            ApplyJournalEntry(
                sequence=1,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.GROUP_STARTED,
            ),
            ApplyJournalEntry(
                sequence=2,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.MEMBER_COMPLETED,
                source_relative_path=video.source_relative_path,
                destination_relative_path=video.destination_relative_path,
            ),
            ApplyJournalEntry(
                sequence=3,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.GROUP_FAILED,
                detail="synthetic interruption",
            ),
        ),
    )
    assert incomplete.completed_group_ids == ()
    assert incomplete.incomplete_group_ids == (group.group_id,)
    assert len(incomplete.completed_members) == 1

    resumed = replay_journal(
        contract,
        (
            ApplyJournalEntry(
                sequence=1,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.GROUP_STARTED,
            ),
            ApplyJournalEntry(
                sequence=2,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.MEMBER_COMPLETED,
                source_relative_path=video.source_relative_path,
                destination_relative_path=video.destination_relative_path,
            ),
            ApplyJournalEntry(
                sequence=3,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.GROUP_FAILED,
                detail="synthetic interruption",
            ),
            ApplyJournalEntry(
                sequence=4,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.GROUP_STARTED,
            ),
            ApplyJournalEntry(
                sequence=5,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.MEMBER_COMPLETED,
                source_relative_path=subtitle.source_relative_path,
                destination_relative_path=subtitle.destination_relative_path,
            ),
            ApplyJournalEntry(
                sequence=6,
                plan_sha256=contract.plan_sha256,
                group_id=group.group_id,
                event=JournalEvent.GROUP_COMPLETED,
            ),
        ),
    )
    assert resumed.completed_group_ids == (group.group_id,)
    assert resumed.incomplete_group_ids == ()
    assert len(resumed.completed_members) == 2


def test_journal_rejects_repeating_an_already_completed_member():
    plan = _plan()
    contract = build_apply_contract(
        plan_to_manifest(plan),
        _preflight(plan),
        _approval(plan),
    )
    group = contract.groups[0]
    video = group.moving_members[0]

    with pytest.raises(ApplyContractError, match="repeats a completed member"):
        replay_journal(
            contract,
            (
                ApplyJournalEntry(
                    sequence=1,
                    plan_sha256=contract.plan_sha256,
                    group_id=group.group_id,
                    event=JournalEvent.GROUP_STARTED,
                ),
                ApplyJournalEntry(
                    sequence=2,
                    plan_sha256=contract.plan_sha256,
                    group_id=group.group_id,
                    event=JournalEvent.MEMBER_COMPLETED,
                    source_relative_path=video.source_relative_path,
                    destination_relative_path=video.destination_relative_path,
                ),
                ApplyJournalEntry(
                    sequence=3,
                    plan_sha256=contract.plan_sha256,
                    group_id=group.group_id,
                    event=JournalEvent.MEMBER_COMPLETED,
                    source_relative_path=video.source_relative_path,
                    destination_relative_path=video.destination_relative_path,
                ),
            ),
        )
