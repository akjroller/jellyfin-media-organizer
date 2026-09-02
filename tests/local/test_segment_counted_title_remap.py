from __future__ import annotations

from collections.abc import Mapping

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    NumberingMode,
    ParseResult,
    ProviderIdentity,
)
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)
from jellyfin_show_organizer.segment_counted_titles import (
    analyze_segment_counted_titles,
    clean_episode_title_hint,
)
from jellyfin_show_organizer.show_resolver import (
    ResolutionStatus,
    resolve_show_group_with_provider,
)

pytestmark = pytest.mark.local

ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


def _episode(show: str, number: int, title: str) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", f"{show}-{number}"),
        season=1,
        number=number,
        title=title,
    )


def _catalog(
    identity: ProviderIdentity,
    episodes: tuple[ProviderEpisode, ...],
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog:{identity.value}:v1",
        show_identity=identity,
        episodes=episodes,
    )


def _correct_catalog() -> ProviderEpisodeCatalog:
    return _catalog(
        ALPHA,
        (
            _episode("alpha", 1, "First Story / Second Story"),
            _episode("alpha", 2, "Third Story / Fourth Story"),
            _episode("alpha", 3, "Fifth Story"),
            _episode("alpha", 4, "Sixth Story"),
        ),
    )


def _wrong_catalog() -> ProviderEpisodeCatalog:
    return _catalog(
        BETA,
        tuple(
            _episode("beta", number, f"Unrelated Story {number}")
            for number in range(1, 7)
        ),
    )


def _parses() -> tuple[ParseResult, ...]:
    return (
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(1, 2),
            title_hint="First Story & Second Story AAC2 0",
        ),
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(3, 4),
            title_hint="Third Story & Fourth Story AAC2 0",
        ),
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(5,),
            title_hint="Fifth Story AAC2 0",
        ),
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(6,),
            title_hint="Sixth Story AAC2 0",
        ),
    )


class Provider:
    provider_name = "fixture"

    def __init__(
        self,
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog] | None = None,
    ) -> None:
        self.catalogs = dict(
            catalogs or {ALPHA: _correct_catalog(), BETA: _wrong_catalog()}
        )
        self.catalog_calls: list[ProviderIdentity] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        assert title == "Example Series"
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key="search:example-series",
            cache_snapshot_id="search:v1",
            shows=(
                ProviderShow(ALPHA, "Example Series", None),
                ProviderShow(BETA, "Example Series", None),
            ),
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        return self.catalogs[show_identity]


def _show() -> CanonicalShow:
    return CanonicalShow(
        source_key="Example Series",
        provider_identity=ALPHA,
        title="Example Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )


def _sources(
    parses: tuple[ParseResult, ...] | None = None,
) -> tuple[SourceEpisodeInput, ...]:
    return tuple(
        SourceEpisodeInput(f"source-{index}.mkv", parse)
        for index, parse in enumerate(parses or _parses(), start=1)
    )


def test_audio_suffix_is_trimmed_before_exact_title_comparison() -> None:
    assert clean_episode_title_hint("First Story & Second Story AAC2 0") == (
        "first story second story"
    )


def test_repeated_same_season_titles_prove_segment_counted_family() -> None:
    analysis = analyze_segment_counted_titles(_parses(), _correct_catalog())

    assert analysis.proven
    assert analysis.exact_match_count == 4
    assert analysis.coordinate_disagreement_count == 4
    assert analysis.one_to_one


def test_exact_titles_choose_correct_show_even_when_wrong_show_fits_coordinates() -> (
    None
):
    provider = Provider()
    resolution = resolve_show_group_with_provider(
        "Example Series",
        _parses(),
        load_overrides(),
        provider,
    )

    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.provider_identity == ALPHA
    assert resolution.evidence.method.endswith("+segment-counted-title-rescue")
    assert "segment-counted-title-rescue:unique-compatible-candidate" in (
        resolution.evidence.reasons
    )


def test_proven_group_remaps_exact_titles_without_arithmetic() -> None:
    result = assign_episode_group_with_provider(_show(), _sources(), Provider())
    by_source = {assignment.source_key: assignment for assignment in result.assignments}

    assert result.status is AssignmentStatus.MATCHED
    assert [
        by_source[f"source-{index}.mkv"].episodes[0].identity.value
        for index in range(1, 5)
    ] == ["alpha-1", "alpha-2", "alpha-3", "alpha-4"]
    assert all(
        "segment-counted-title-remap:group-proven" in assignment.evidence.reasons
        for assignment in result.assignments
    )


def test_unconfirmed_aired_source_stays_blocked_after_group_is_proven() -> None:
    parses = (
        *_parses(),
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(7,),
            title_hint="Unknown Story AAC2 0",
        ),
    )
    result = assign_episode_group_with_provider(_show(), _sources(parses), Provider())
    by_source = {assignment.source_key: assignment for assignment in result.assignments}

    assert by_source["source-5.mkv"].status is AssignmentStatus.UNRESOLVED
    assert not by_source["source-5.mkv"].episodes
    assert "segment-counted-title-remap:missing-exact-title-proof" in (
        by_source["source-5.mkv"].evidence.reasons
    )


def test_ordinary_coordinate_aligned_aired_group_is_unchanged() -> None:
    parses = tuple(
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(number,),
            title_hint=title,
        )
        for number, title in (
            (1, "First Story / Second Story"),
            (2, "Third Story / Fourth Story"),
            (3, "Fifth Story"),
        )
    )
    result = assign_episode_group_with_provider(_show(), _sources(parses), Provider())

    assert result.status is AssignmentStatus.MATCHED
    assert all(
        "segment-counted-title-remap:group-proven" not in assignment.evidence.reasons
        for assignment in result.assignments
    )


def test_insufficient_exact_title_evidence_does_not_prove_remap() -> None:
    analysis = analyze_segment_counted_titles(_parses()[:2], _correct_catalog())

    assert not analysis.proven
    assert analysis.exact_match_count == 2


def test_ambiguous_catalog_title_blocks_family_proof() -> None:
    catalog = _catalog(
        ALPHA,
        (
            *_correct_catalog().episodes,
            ProviderEpisode(
                identity=ProviderIdentity("fixture", "alpha-duplicate"),
                season=1,
                number=8,
                title="Fifth Story",
            ),
        ),
    )
    analysis = analyze_segment_counted_titles(_parses(), catalog)

    assert not analysis.proven
    assert analysis.ambiguous_count == 1


def test_multiple_sources_cannot_collapse_to_same_provider_episode() -> None:
    parses = (
        *_parses(),
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(7,),
            title_hint="Fifth Story AAC2 0",
        ),
    )
    analysis = analyze_segment_counted_titles(parses, _correct_catalog())

    assert not analysis.proven
    assert not analysis.one_to_one


def test_provider_episode_duplicate_guard_runs_after_title_remap() -> None:
    sources = (
        *_sources(),
        SourceEpisodeInput("absolute.mkv", ParseResult(absolute_episode=1)),
    )
    result = assign_episode_group_with_provider(_show(), sources, Provider())
    by_source = {assignment.source_key: assignment for assignment in result.assignments}

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert by_source["source-1.mkv"].status is AssignmentStatus.SUSPICIOUS
    assert by_source["absolute.mkv"].status is AssignmentStatus.SUSPICIOUS
    assert any(
        reason.startswith("duplicate-provider-episode-assignment:")
        for reason in by_source["source-1.mkv"].evidence.reasons
    )


def test_input_order_does_not_change_remapped_assignments() -> None:
    forward = assign_episode_group_with_provider(_show(), _sources(), Provider())
    reverse = assign_episode_group_with_provider(
        _show(), tuple(reversed(_sources())), Provider()
    )

    assert forward == reverse
