from __future__ import annotations

import copy

import pytest

from mnamer.organizer.models import (
    PLAN_SCHEMA_VERSION,
    CanonicalShow,
    DuplicateDecision,
    DuplicateDisposition,
    EpisodeMatch,
    MatchEvidence,
    MatchMethod,
    NumberingMode,
    OrganizerPlan,
    ParsedEpisode,
    PlanItem,
    PlanStatus,
    SourceFingerprint,
    SourceVideo,
)

pytestmark = pytest.mark.local


def _matched_item(
    source_path: str,
    episode: int,
    *,
    reverse_evidence: bool = False,
) -> PlanItem:
    evidence: tuple[MatchEvidence, ...] = (
        MatchEvidence("series-title", "exact", 1.0),
        MatchEvidence("episode-number", f"S01E{episode:02}", 1.0),
    )
    if reverse_evidence:
        evidence = tuple(reversed(evidence))
    return PlanItem(
        source=SourceVideo(
            source_path=source_path,
            extension="MKV",
            fingerprint=SourceFingerprint(
                size_bytes=episode * 1000,
                modified_ns=episode * 100,
                sha256="A" * 64,
            ),
        ),
        parsed=ParsedEpisode(
            series="Example Show",
            season=1,
            episodes=(episode,),
            episode_title=f"Part {episode}",
        ),
        status=PlanStatus.MATCHED,
        destination=(
            "organized/Example Show [tvmazeid-123]/Season 01/"
            f"Example Show - S01E{episode:02} - Part {episode}.mkv"
        ),
        match=EpisodeMatch(
            show=CanonicalShow(tvmaze_id=123, name="Example Show", year=2020),
            season=1,
            episode=episode,
            title=f"Part {episode}",
            method=MatchMethod.SEASON_EPISODE,
            confidence=1.0,
            evidence=evidence,
        ),
    )


def test_numbering_modes_are_an_explicit_stable_contract():
    assert {mode.value for mode in NumberingMode} == {
        "aired",
        "absolute",
        "parenthesized-absolute",
        "segment-title",
    }


def test_parsed_episode_preserves_all_multi_episode_numbers():
    parsed = ParsedEpisode(
        series="Segmented Show",
        season=1,
        episodes=(4, 3, 4),
        episode_title="First and Second",
    )

    assert parsed.episodes == (3, 4)


def test_plan_hash_is_stable_across_input_and_evidence_order():
    first = _matched_item("fixtures/shows/example-01.mkv", 1)
    second = _matched_item("fixtures/shows/example-02.mkv", 2, reverse_evidence=True)

    plan_a = OrganizerPlan(
        source_root="fixtures/shows",
        overrides_version=1,
        items=(first, second),
    )
    plan_b = OrganizerPlan(
        source_root="fixtures/shows",
        overrides_version=1,
        items=(second, first),
    )

    assert plan_a.plan_hash == plan_b.plan_hash
    assert plan_a.to_manifest_json() == plan_b.to_manifest_json()
    assert len(plan_a.plan_hash) == 64


def test_manifest_round_trip_validates_schema_and_hash():
    plan = OrganizerPlan(
        source_root="fixtures/shows",
        overrides_version=1,
        items=(_matched_item("fixtures/shows/example-01.mkv", 1),),
    )

    restored = OrganizerPlan.from_manifest(plan.to_manifest())

    assert restored == plan
    assert restored.schema_version == PLAN_SCHEMA_VERSION
    assert restored.plan_hash == plan.plan_hash


def test_manifest_rejects_tampering_and_unknown_schema():
    plan = OrganizerPlan(
        source_root="fixtures/shows",
        overrides_version=1,
        items=(_matched_item("fixtures/shows/example-01.mkv", 1),),
    )
    tampered = copy.deepcopy(plan.to_manifest())
    tampered["source_root"] = "fixtures/other"

    with pytest.raises(ValueError, match="plan hash does not match"):
        OrganizerPlan.from_manifest(tampered)

    unsupported = copy.deepcopy(plan.to_manifest())
    unsupported["schema_version"] = PLAN_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="unsupported plan schema version"):
        OrganizerPlan.from_manifest(unsupported)


def test_plan_rejects_case_insensitive_duplicate_source_paths():
    first = _matched_item("fixtures/shows/Episode.mkv", 1)
    second = _matched_item("FIXTURES/SHOWS/EPISODE.MKV", 2)

    with pytest.raises(ValueError, match="unique case-insensitively"):
        OrganizerPlan(
            source_root="fixtures/shows",
            overrides_version=1,
            items=(first, second),
        )


def test_matched_item_requires_match_and_destination():
    source = SourceVideo(
        source_path="fixtures/shows/unresolved.mkv",
        extension=".mkv",
        fingerprint=SourceFingerprint(size_bytes=100, modified_ns=200),
    )

    with pytest.raises(ValueError, match="require a match and destination"):
        PlanItem(
            source=source,
            parsed=ParsedEpisode(series="Unknown"),
            status=PlanStatus.MATCHED,
        )


def test_duplicate_decision_is_non_destructive_plan_data():
    decision = DuplicateDecision(
        group_id="duplicate-001",
        destination="organized/Example Show/episode.mkv",
        candidates=("fixtures/low.mkv", "fixtures/high.mkv"),
        disposition=DuplicateDisposition.PROPOSED,
        reason="higher resolution",
        winner_source="fixtures/high.mkv",
        quarantine_sources=("fixtures/low.mkv",),
    )

    assert decision.winner_source == "fixtures/high.mkv"
    assert decision.quarantine_sources == ("fixtures/low.mkv",)
