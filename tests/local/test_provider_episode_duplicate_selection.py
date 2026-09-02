from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from jellyfin_show_organizer.destination import DestinationPolicy
from jellyfin_show_organizer.extra_classifier import (
    ExtraClassification,
    ExtraDisposition,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    CompanionStatus,
    MatchEvidence,
    NumberingMode,
    ParseResult,
    ProviderIdentity,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.overrides import (
    DuplicatePreferenceOverride,
    OverrideCatalog,
)
from jellyfin_show_organizer.planner import (
    _apply_duplicate_decisions,
    _plan_companions,
    _plan_resolved_group,
)
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
)
from jellyfin_show_organizer.release_quality import (
    ReleaseSourceFamily,
    parse_release_quality,
)
from jellyfin_show_organizer.show_resolver import ResolutionStatus, ShowResolution
from jellyfin_show_organizer.sidecars import (
    AdjacentDisposition,
    AdjacentFile,
    CompanionGroup,
    CompanionKind,
    SidecarDiscovery,
)

pytestmark = pytest.mark.local

SHOW_ID = ProviderIdentity("fixture", "show-1")
EPISODE_1 = ProviderEpisode(
    identity=ProviderIdentity("fixture", "episode-1"),
    season=1,
    number=1,
    title="First Signal",
)
EPISODE_2 = ProviderEpisode(
    identity=ProviderIdentity("fixture", "episode-2"),
    season=1,
    number=2,
    title="Second Signal",
)
SHOW = CanonicalShow(
    source_key="Fabricated Series",
    provider_identity=SHOW_ID,
    title="Fabricated Series",
    year=2024,
    numbering_mode=NumberingMode.AIRED,
)
RESOLUTION = ShowResolution(
    status=ResolutionStatus.MATCHED,
    show=SHOW,
    evidence=MatchEvidence(method="fabricated-show", confidence=1.0),
)
EMPTY_SIDECARS = SidecarDiscovery(companions=(), unresolved=(), ignored=())


class FixtureProvider:
    provider_name = "fixture"

    def __init__(self) -> None:
        self.catalog = ProviderEpisodeCatalog(
            provider="fixture",
            request_key="episodes:show-1",
            cache_snapshot_id="fixture-catalog-v1",
            show_identity=SHOW_ID,
            episodes=(EPISODE_1, EPISODE_2),
        )

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search:{title}:empty",
            shows=(),
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW_ID
        return self.catalog


def _source(path: str, *, sha256: str | None = None) -> SourceFile:
    return SourceFile(
        relative_path=path,
        extension=PurePosixPath(path).suffix,
        fingerprint=SourceFingerprint(
            size=100,
            mtime_ns=10,
            sha256=sha256,
        ),
    )


def _classification(parse: ParseResult) -> ExtraClassification:
    return ExtraClassification(
        disposition=ExtraDisposition.EPISODE_CANDIDATE,
        parse=parse,
        reasons=("fabricated episode evidence",),
    )


def _records(
    source_parses: tuple[tuple[SourceFile, ParseResult], ...],
    *,
    overrides: OverrideCatalog | None = None,
):
    catalog = overrides or OverrideCatalog(schema_version=2, shows=())
    sources = tuple(source for source, _parse in source_parses)
    classifications = {
        source.relative_path: _classification(parse) for source, parse in source_parses
    }
    planned = _plan_resolved_group(
        sources,
        classifications,
        RESOLUTION,
        FixtureProvider(),
        DestinationPolicy(),
        catalog,
    )
    return _apply_duplicate_decisions(planned, EMPTY_SIDECARS, catalog)


def _by_source(records):
    return {record.source.relative_path: record for record in records}


def test_equivalent_provider_episode_claims_use_release_quality_winner() -> None:
    high = _source("Fabricated Series/release-a.1080p.WEB-DL.mkv")
    low = _source("Fabricated Series/release-b.720p.WEB-DL.mp4")

    records = _by_source(
        _records(
            (
                (low, ParseResult(season=1, episodes=(1,))),
                (high, ParseResult(season=1, episodes=(1,))),
            )
        )
    )

    assert records[high.relative_path].status is TerminalStatus.MATCHED
    assert records[low.relative_path].status is TerminalStatus.DUPLICATE
    decision = records[high.relative_path].duplicate
    assert decision is not None
    assert decision.winner == high.relative_path
    assert decision.losers == (low.relative_path,)
    assert decision.destination_key.startswith("provider-episode-collision:")
    assert "release-quality-winner:" + high.relative_path in decision.evidence
    assert any("no deletion is authorized" in reason for reason in decision.evidence)


def test_equal_quality_provider_episode_claims_remain_blocking() -> None:
    first = _source("Fabricated Series/release-a.1080p.WEB-DL.mkv")
    second = _source("Fabricated Series/release-b.1080p.WEB-DL.mp4")

    records = _by_source(
        _records(
            (
                (first, ParseResult(season=1, episodes=(1,))),
                (second, ParseResult(season=1, episodes=(1,))),
            )
        )
    )

    assert records[first.relative_path].status is TerminalStatus.SUSPICIOUS
    assert records[second.relative_path].status is TerminalStatus.SUSPICIOUS
    decision = records[first.relative_path].duplicate
    assert decision is not None
    assert decision.winner is None
    assert records[second.relative_path].duplicate == decision


def test_explicit_duplicate_preference_can_select_equivalent_claim_winner() -> None:
    first = _source("Fabricated Series/release-a.1080p.WEB-DL.mkv")
    second = _source("Fabricated Series/release-b.1080p.WEB-DL.mp4")
    overrides = OverrideCatalog(
        schema_version=2,
        shows=(),
        duplicate_preferences=(
            DuplicatePreferenceOverride(
                source=second.relative_path,
                rank=100,
                reasons=("fabricated reviewed preference",),
            ),
        ),
    )

    records = _by_source(
        _records(
            (
                (first, ParseResult(season=1, episodes=(1,))),
                (second, ParseResult(season=1, episodes=(1,))),
            ),
            overrides=overrides,
        )
    )

    assert records[second.relative_path].status is TerminalStatus.MATCHED
    assert records[first.relative_path].status is TerminalStatus.DUPLICATE
    decision = records[second.relative_path].duplicate
    assert decision is not None
    assert decision.winner == second.relative_path
    assert any("explicit preference rank 100" in reason for reason in decision.evidence)


def test_three_way_equivalent_claims_are_one_deterministic_group() -> None:
    high = _source("Fabricated Series/release-a.1080p.WEB-DL.mkv")
    medium = _source("Fabricated Series/release-b.720p.WEB-DL.mp4")
    low = _source("Fabricated Series/release-c.480p.WEB-DL.avi")

    records = _by_source(
        _records(
            tuple(
                (source, ParseResult(season=1, episodes=(1,)))
                for source in (low, high, medium)
            )
        )
    )

    decision = records[high.relative_path].duplicate
    assert decision is not None
    assert decision.winner == high.relative_path
    assert decision.candidates == tuple(
        sorted(
            (high.relative_path, medium.relative_path, low.relative_path),
            key=lambda value: (value.casefold(), value),
        )
    )
    assert records[medium.relative_path].status is TerminalStatus.DUPLICATE
    assert records[low.relative_path].status is TerminalStatus.DUPLICATE


def test_identical_multi_episode_claim_sets_can_reach_duplicate_selection() -> None:
    high = _source("Fabricated Series/double-a.1080p.WEB-DL.mkv")
    low = _source("Fabricated Series/double-b.720p.WEB-DL.mp4")

    records = _by_source(
        _records(
            (
                (high, ParseResult(season=1, episodes=(1, 2))),
                (low, ParseResult(season=1, episodes=(1, 2))),
            )
        )
    )

    assert records[high.relative_path].status is TerminalStatus.MATCHED
    assert records[low.relative_path].status is TerminalStatus.DUPLICATE
    assert [
        episode.provider_identity
        for episode in records[high.relative_path].provider_episodes
    ] == [EPISODE_1.identity, EPISODE_2.identity]


def test_partial_provider_episode_overlap_remains_suspicious() -> None:
    double = _source("Fabricated Series/double.1080p.WEB-DL.mkv")
    single = _source("Fabricated Series/single.720p.WEB-DL.mp4")

    records = _by_source(
        _records(
            (
                (double, ParseResult(season=1, episodes=(1, 2))),
                (single, ParseResult(season=1, episodes=(2,))),
            )
        )
    )

    assert records[double.relative_path].status is TerminalStatus.SUSPICIOUS
    assert records[single.relative_path].status is TerminalStatus.SUSPICIOUS
    assert records[double.relative_path].provider_episodes == ()
    assert records[single.relative_path].provider_episodes == ()
    assert records[double.relative_path].duplicate is None
    assert records[single.relative_path].duplicate is None


def test_selected_video_sidecars_follow_winner_and_loser_states() -> None:
    high = _source("Fabricated Series/release-a.1080p.WEB-DL.mkv")
    low = _source("Fabricated Series/release-b.720p.WEB-DL.mp4")
    discovery = SidecarDiscovery(
        companions=(
            CompanionGroup(
                source_video=high.relative_path,
                kind=CompanionKind.SUBTITLE,
                suffix=".en",
                files=(
                    AdjacentFile(
                        relative_path="Fabricated Series/release-a.1080p.WEB-DL.en.srt",
                        extension=".srt",
                        fingerprint=SourceFingerprint(size=10, mtime_ns=20),
                        disposition=AdjacentDisposition.ASSOCIATED,
                        reason="fabricated subtitle",
                    ),
                ),
            ),
            CompanionGroup(
                source_video=low.relative_path,
                kind=CompanionKind.SUBTITLE,
                suffix=".en",
                files=(
                    AdjacentFile(
                        relative_path="Fabricated Series/release-b.720p.WEB-DL.en.srt",
                        extension=".srt",
                        fingerprint=SourceFingerprint(size=10, mtime_ns=20),
                        disposition=AdjacentDisposition.ASSOCIATED,
                        reason="fabricated subtitle",
                    ),
                ),
            ),
        ),
        unresolved=(),
        ignored=(),
    )
    classifications = {
        high.relative_path: _classification(ParseResult(season=1, episodes=(1,))),
        low.relative_path: _classification(ParseResult(season=1, episodes=(1,))),
    }
    planned = _plan_resolved_group(
        (high, low),
        classifications,
        RESOLUTION,
        FixtureProvider(),
        DestinationPolicy(),
        OverrideCatalog(schema_version=2, shows=()),
    )
    finalized = _apply_duplicate_decisions(
        planned,
        discovery,
        OverrideCatalog(schema_version=2, shows=()),
    )
    companions = _plan_companions(discovery, tuple(finalized))
    by_source = {record.relative_path: record for record in companions}

    assert (
        by_source["Fabricated Series/release-a.1080p.WEB-DL.en.srt"].status
        is CompanionStatus.ASSOCIATED
    )
    assert (
        by_source["Fabricated Series/release-b.720p.WEB-DL.en.srt"].status
        is CompanionStatus.DUPLICATE
    )


def test_provider_episode_duplicate_selection_is_input_order_deterministic() -> None:
    high = _source("Fabricated Series/release-a.1080p.WEB-DL.mkv")
    low = _source("Fabricated Series/release-b.720p.WEB-DL.mp4")
    source_parses = (
        (high, ParseResult(season=1, episodes=(1,))),
        (low, ParseResult(season=1, episodes=(1,))),
    )

    forward = _by_source(_records(source_parses))
    reverse = _by_source(_records(tuple(reversed(source_parses))))

    assert forward.keys() == reverse.keys()
    for source in forward:
        assert forward[source].status == reverse[source].status
        assert forward[source].duplicate == reverse[source].duplicate
        assert forward[source].provider_episodes == reverse[source].provider_episodes


def test_bd_remux_tokens_are_bluray_remux_without_changing_mode_policy() -> None:
    spaced = parse_release_quality(
        "Fabricated Series/Fabricated.S01E01.1080p.BD Remux.mkv"
    )
    hyphenated = parse_release_quality(
        "Fabricated Series/Fabricated.S01E01.1080p.BD-Remux.mkv"
    )

    for quality in (spaced, hyphenated):
        assert quality.source_family is ReleaseSourceFamily.BLURAY
        assert quality.remux is True
        assert quality.errors == ()
