"""Plan value objects refuse every malformed construction."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from jellyfin_show_organizer.models import (
    CacheSnapshot,
    CandidateEvidence,
    CanonicalShow,
    CompanionPlanRecord,
    CompanionStatus,
    DuplicateCollisionClass,
    DuplicateDecision,
    ExtraDecision,
    MatchEvidence,
    OrganizerPlan,
    ParseResult,
    PlanEpisode,
    PlanProvenance,
    PlanRecord,
    ProviderIdentity,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)

pytestmark = pytest.mark.local

HASH = "a" * 64
FINGERPRINT = SourceFingerprint(size=1, mtime_ns=1)
SOURCE = SourceFile("Show/episode.mkv", ".mkv", FINGERPRINT)
EVIDENCE = MatchEvidence(method="test", confidence=1.0)
SHOW = CanonicalShow("Show", tvmaze_id=1, title="Show")


def _duplicate(**overrides: object) -> DuplicateDecision:
    values: dict[str, object] = {
        "destination_key": "show/s01e01.mkv",
        "candidates": ("a.mkv", "b.mkv"),
        "winner": "a.mkv",
        "losers": ("b.mkv",),
        "confidence": 1.0,
    }
    values.update(overrides)
    return DuplicateDecision(**values)  # type: ignore[arg-type]


INVALID: dict[str, Callable[[], object]] = {
    "provider name not normalized": lambda: ProviderIdentity("Bad Name!", "1"),
    "provider value empty": lambda: ProviderIdentity("tvmaze", " "),
    "tvmaze id not positive": lambda: ProviderIdentity.tvmaze(0),
    "identity not an integer": lambda: ProviderIdentity(
        "tvmaze", "x"
    ).require_positive_int("tvmaze"),
    "identity from another provider": lambda: ProviderIdentity(
        "tmdb", "1"
    ).require_positive_int("tvmaze"),
    "identity not canonical": lambda: ProviderIdentity(
        "tvmaze", "01"
    ).require_positive_int("tvmaze"),
    "fingerprint negative size": lambda: SourceFingerprint(size=-1, mtime_ns=0),
    "fingerprint negative mtime": lambda: SourceFingerprint(size=0, mtime_ns=-1),
    "fingerprint short sha256": lambda: SourceFingerprint(0, 0, sha256="ab"),
    "fingerprint non-hex sha256": lambda: SourceFingerprint(0, 0, sha256="z" * 64),
    "source path empty": lambda: SourceFile("", ".mkv", FINGERPRINT),
    "source extension without dot": lambda: SourceFile("a.mkv", "mkv", FINGERPRINT),
    "three series aliases": lambda: ParseResult(series_aliases=("a", "b", "c")),
    "untrimmed alias": lambda: ParseResult(series_aliases=(" a",)),
    "duplicate aliases": lambda: ParseResult(series_aliases=("A-B", "a b")),
    "negative season": lambda: ParseResult(season=-1),
    "negative episode": lambda: ParseResult(episodes=(-1,)),
    "negative absolute": lambda: ParseResult(absolute_episode=-1),
    "zero in absolute list": lambda: ParseResult(absolute_episodes=(0,)),
    "absolute and list together": lambda: ParseResult(
        absolute_episode=1, absolute_episodes=(2,)
    ),
    "repeated absolute": lambda: ParseResult(absolute_episodes=(2, 2)),
    "special kind without number": lambda: ParseResult(special_kind="ova"),
    "unknown special kind": lambda: ParseResult(
        special_kind="movie", special_episode=1
    ),
    "special number zero": lambda: ParseResult(special_kind="ova", special_episode=0),
    "invalid date": lambda: ParseResult(episode_date="2024-02-30"),
    "non-canonical date": lambda: ParseResult(episode_date="2024-1-01"),
    "ancient year": lambda: ParseResult(year=1700),
    "embedded id zero": lambda: ParseResult(embedded_tvmaze_id=0),
    "conflicting embedded ids": lambda: ParseResult(
        embedded_tvmaze_id=1,
        embedded_provider_identity=ProviderIdentity.tvmaze(2),
    ),
    "show without identity": lambda: CanonicalShow("Show"),
    "show with conflicting identity": lambda: CanonicalShow(
        "Show", tvmaze_id=1, provider_identity=ProviderIdentity.tvmaze(2)
    ),
    "candidate without identity": lambda: CandidateEvidence(title="Show"),
    "candidate conflicting identity": lambda: CandidateEvidence(
        1, "Show", provider_identity=ProviderIdentity.tvmaze(2)
    ),
    "candidate without title": lambda: CandidateEvidence(1, ""),
    "candidate score above one": lambda: CandidateEvidence(1, "Show", score=2.0),
    "evidence without method": lambda: MatchEvidence(method="", confidence=0.5),
    "evidence confidence below zero": lambda: MatchEvidence(method="m", confidence=-1),
    "extra without rule": lambda: ExtraDecision(kind="trailer", rule=""),
    "duplicate without key": lambda: _duplicate(destination_key=""),
    "duplicate single candidate": lambda: _duplicate(candidates=("a.mkv",)),
    "duplicate confidence above one": lambda: _duplicate(confidence=2.0),
    "duplicate winner not a candidate": lambda: _duplicate(winner="c.mkv"),
    "duplicate loser not a candidate": lambda: _duplicate(losers=("c.mkv",)),
    "conflict with a winner": lambda: _duplicate(
        collision_class=DuplicateCollisionClass.DESTINATION_CONFLICT
    ),
    "episode without identity": lambda: PlanEpisode(title="Pilot"),
    "episode conflicting identity": lambda: PlanEpisode(
        1, 1, 1, "Pilot", provider_identity=ProviderIdentity.tvmaze(2)
    ),
    "episode negative season": lambda: PlanEpisode(1, -1, 1, "Pilot"),
    "episode negative number": lambda: PlanEpisode(1, 1, -1, "Pilot"),
    "episode without title": lambda: PlanEpisode(1, 1, 1, ""),
    "episode invalid airdate": lambda: PlanEpisode(1, 1, 1, "Pilot", "2024-13-01"),
    "episode non-canonical airdate": lambda: PlanEpisode(1, 1, 1, "Pilot", "2024-1-01"),
    "snapshot missing field": lambda: CacheSnapshot("", "search", "k", HASH, "ok"),
    "snapshot short id": lambda: CacheSnapshot("tvmaze", "search", "k", "ab", "ok"),
    "snapshot non-hex id": lambda: CacheSnapshot(
        "tvmaze", "search", "k", "z" * 64, "ok"
    ),
    "provenance without version": lambda: PlanProvenance("", HASH, HASH),
    "provenance short config id": lambda: PlanProvenance("1", "ab", HASH),
    "provenance non-hex overrides id": lambda: PlanProvenance("1", HASH, "z" * 64),
    "companion without path": lambda: CompanionPlanRecord(
        "", ".srt", None, CompanionStatus.IGNORED, "r"
    ),
    "companion extension without dot": lambda: CompanionPlanRecord(
        "a.srt", "srt", None, CompanionStatus.IGNORED, "r"
    ),
    "companion without reason": lambda: CompanionPlanRecord(
        "a.srt", ".srt", None, CompanionStatus.IGNORED, ""
    ),
    "associated companion without group": lambda: CompanionPlanRecord(
        "a.srt", ".srt", FINGERPRINT, CompanionStatus.ASSOCIATED, "r"
    ),
    "matched record without destination": lambda: PlanRecord(
        SOURCE, TerminalStatus.MATCHED, ParseResult(), SHOW, EVIDENCE
    ),
    "extra record without extra": lambda: PlanRecord(SOURCE, TerminalStatus.EXTRA),
    "duplicate record without duplicate": lambda: PlanRecord(
        SOURCE, TerminalStatus.DUPLICATE
    ),
    "held record with destination": lambda: PlanRecord(
        SOURCE, TerminalStatus.HELD, evidence=EVIDENCE, reason="r", destination="x"
    ),
    "held record with episodes": lambda: PlanRecord(
        SOURCE,
        TerminalStatus.HELD,
        evidence=EVIDENCE,
        reason="r",
        provider_episodes=(PlanEpisode(1, 1, 1, "Pilot"),),
    ),
    "held record without reason": lambda: PlanRecord(
        SOURCE, TerminalStatus.HELD, evidence=EVIDENCE
    ),
    "plan schema zero": lambda: OrganizerPlan(0, 1, ()),
    "plan overrides zero": lambda: OrganizerPlan(3, 0, ()),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_malformed_models_are_refused(case: str) -> None:
    with pytest.raises(ValueError):
        INVALID[case]()


def test_valid_models_expose_their_identities() -> None:
    identity = ProviderIdentity(" TVMaze ", " 42 ")
    assert identity.key == "tvmaze:42"
    assert identity.require_positive_int("TVMAZE") == 42

    parse = ParseResult(
        series_aliases=("A", "B"),
        special_kind="OVA",
        special_episode=1,
        episode_date="2024-01-02",
        embedded_tvmaze_id=7,
        embedded_provider_identity=ProviderIdentity.tvmaze(7),
    )
    assert parse.special_kind == "ova"
    assert parse.provider_identities == (ProviderIdentity.tvmaze(7),)

    candidate = CandidateEvidence(3, "Show", 0.5)
    assert (candidate.provider, candidate.provider_id, candidate.tvmaze_id) == (
        "tvmaze",
        "3",
        3,
    )
    episode = PlanEpisode(9, 1, 2, "Pilot", "2024-01-02")
    assert (episode.provider, episode.provider_id) == ("tvmaze", "9")
    assert SHOW.provider_identity == ProviderIdentity.tvmaze(1)
