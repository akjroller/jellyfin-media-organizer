from dataclasses import replace

import pytest

from jellyfin_show_organizer.apply_contract import (
    ApplyApproval,
    ApplyContractError,
    ApplyJournalEntry,
    ApplyMemberRole,
    JournalEvent,
    build_apply_contract,
    replay_journal,
)
from jellyfin_show_organizer.models import (
    CacheSnapshot,
    CanonicalShow,
    CompanionPlanRecord,
    CompanionStatus,
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
from jellyfin_show_organizer.schema import PLAN_SCHEMA_VERSION, plan_to_manifest, stable_plan_hash

pytestmark = pytest.mark.local


def _cache_snapshot() -> CacheSnapshot:
    return CacheSnapshot(
        provider="tvmaze",
        kind="episode-catalog",
        request_key="45001",
        snapshot_id="d" * 64,
        state="ok",
    )


def _plan(*, video_status: TerminalStatus = TerminalStatus.MATCHED) -> OrganizerPlan:
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
            "Example Series (2024)/Season 01/"
            "Example Series (2024) S01E01 - Pilot.mkv"
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
        overrides_version=2,
        records=(record,),
        companions=(companion,),
        provenance=PlanProvenance(
            tool_version="0.1.0",
            config_snapshot_id="a" * 64,
            overrides_snapshot_id="b" * 64,
            cache_snapshots=(_cache_snapshot(),),
        ),
    )


def _approval(plan: OrganizerPlan) -> ApplyApproval:
    assert plan.provenance is not None
    return ApplyApproval(
        plan_sha256=stable_plan_hash(plan),
        schema_version=plan.schema_version,
        tool_version=plan.provenance.tool_version,
        config_snapshot_id=plan.provenance.config_snapshot_id,
        overrides_snapshot_id=plan.provenance.overrides_snapshot_id,
        cache_snapshots=plan.provenance.cache_snapshots,
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
