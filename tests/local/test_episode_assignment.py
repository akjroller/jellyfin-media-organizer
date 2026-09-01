from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    NumberingMode,
    ParseResult,
)
from jellyfin_show_organizer.tvmaze_cache import TvmazeCatalogCache

CATALOG = [
    {"id": 9001, "season": 0, "number": 1, "name": "Preview Special"},
    {"id": 1001, "season": 1, "number": 1, "name": "Part Alpha"},
    {"id": 1002, "season": 1, "number": 2, "name": "Part Beta"},
    {"id": 2001, "season": 2, "number": 1, "name": "Return"},
]


class CountingGetter:
    def __init__(self, response: object = CATALOG) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        return self.response


def _show(mode: NumberingMode = NumberingMode.AIRED) -> CanonicalShow:
    return CanonicalShow(
        source_key="Example Series",
        tvmaze_id=4242,
        title="Example Series",
        year=2024,
        numbering_mode=mode,
    )


def test_aired_assignment_preserves_specials_and_multi_episode_order(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()
    cache = TvmazeCatalogCache(tmp_path / "cache")

    result = assign_episode_group(
        _show(),
        (
            SourceEpisodeInput(
                "special.mkv",
                ParseResult(season=0, episodes=(1,)),
            ),
            SourceEpisodeInput(
                "double.mkv",
                ParseResult(season=1, episodes=(1, 2)),
            ),
        ),
        cache,
        getter,
    )

    assert result.status is AssignmentStatus.MATCHED
    assert len(result.assignments) == 2
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    assert [episode.season for episode in by_source["special.mkv"].episodes] == [0]
    assert [
        episode.tvmaze_episode_id for episode in by_source["double.mkv"].episodes
    ] == [1001, 1002]
    assert len(getter.calls) == 1
    assert "numbering-mode:aired" in by_source["double.mkv"].evidence.reasons


def test_absolute_assignment_uses_regular_catalog_order_and_skips_specials(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(NumberingMode.ABSOLUTE),
        (
            SourceEpisodeInput("one.mkv", ParseResult(absolute_episode=1)),
            SourceEpisodeInput("three.mkv", ParseResult(absolute_episode=3)),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.MATCHED
    assert result.assignments[0].episodes[0].tvmaze_episode_id == 1001
    assert result.assignments[1].episodes[0].tvmaze_episode_id == 2001
    assert all(assignment.episodes[0].season > 0 for assignment in result.assignments)


def test_parenthesized_absolute_records_explicit_policy(tmp_path: Path) -> None:
    result = assign_episode_group(
        _show(NumberingMode.PARENTHESIZED_ABSOLUTE),
        (SourceEpisodeInput("two.mkv", ParseResult(absolute_episode=2)),),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assert result.status is AssignmentStatus.MATCHED
    assignment = result.assignments[0]
    assert assignment.episodes[0].tvmaze_episode_id == 1002
    assert "numbering-mode:parenthesized-absolute" in assignment.evidence.reasons


def test_independent_aired_and_absolute_families_assign_against_same_catalog(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(),
        (
            SourceEpisodeInput("aired.mkv", ParseResult(season=1, episodes=(1,))),
            SourceEpisodeInput("absolute.mkv", ParseResult(absolute_episode=2)),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.MATCHED
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    assert by_source["aired.mkv"].episodes[0].tvmaze_episode_id == 1001
    assert by_source["absolute.mkv"].episodes[0].tvmaze_episode_id == 1002
    assert "mixed-numbering-family:absolute" in by_source["absolute.mkv"].evidence.reasons
    assert len(getter.calls) == 1


def test_numbering_policy_conflict_fails_closed(tmp_path: Path) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(NumberingMode.ABSOLUTE),
        (SourceEpisodeInput("aired.mkv", ParseResult(season=1, episodes=(1,))),),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert not getter.calls
    assert (
        result.assignments[0]
        .evidence.reasons[0]
        .startswith("numbering-policy-conflict:")
    )


def test_missing_catalog_entry_is_unresolved_without_partial_match(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(),
        (SourceEpisodeInput("missing.mkv", ParseResult(season=1, episodes=(1, 99))),),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.UNRESOLVED
    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert assignment.episodes == ()
    assert "missing-aired-catalog-entry:S01E99" in assignment.evidence.reasons


def test_duplicate_provider_coordinate_rejects_catalog(tmp_path: Path) -> None:
    duplicate_catalog = [
        {"id": 1001, "season": 1, "number": 1, "name": "Alpha"},
        {"id": 1002, "season": 1, "number": 1, "name": "Other Alpha"},
    ]
    result = assign_episode_group(
        _show(),
        (SourceEpisodeInput("one.mkv", ParseResult(season=1, episodes=(1,))),),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(duplicate_catalog),
    )

    assert result.status is AssignmentStatus.UNRESOLVED
    assert "duplicate-aired-coordinate:S01E01" in result.assignments[0].evidence.reasons


def test_segment_title_mode_preserves_distinct_segment_evidence(tmp_path: Path) -> None:
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "segment-a.mkv",
                ParseResult(
                    season=1,
                    episodes=(1,),
                    segment_hint="a",
                    title_hint="Part Alpha",
                ),
            ),
            SourceEpisodeInput(
                "segment-b.mkv",
                ParseResult(
                    season=1,
                    episodes=(1,),
                    segment_hint="b",
                    title_hint="Part Beta",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assert result.status is AssignmentStatus.MATCHED
    assert [
        assignment.episodes[0].tvmaze_episode_id for assignment in result.assignments
    ] == [
        1001,
        1002,
    ]
    assert "segment-hint:a" in result.assignments[0].evidence.reasons
    assert "segment-hint:b" in result.assignments[1].evidence.reasons


def test_distinct_segments_cannot_collapse_to_same_provider_episode(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "segment-a.mkv",
                ParseResult(segment_hint="a", title_hint="Part Alpha"),
            ),
            SourceEpisodeInput(
                "segment-b.mkv",
                ParseResult(segment_hint="b", title_hint="Part Alpha"),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert all(assignment.episodes == () for assignment in result.assignments)
    assert all(
        "distinct-segments-collapse-to-same-catalog-episode"
        in assignment.evidence.reasons
        for assignment in result.assignments
    )


def test_explicit_provider_identity_conflict_stops_before_catalog_fetch(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(),
        (
            SourceEpisodeInput(
                "wrong-id.mkv",
                ParseResult(
                    season=1,
                    episodes=(1,),
                    embedded_tvmaze_id=9999,
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert not getter.calls
    assert (
        "source-provider-id-conflicts-with-canonical-show"
        in result.assignments[0].evidence.reasons
    )


def test_warm_cache_replays_equivalent_group_without_http_calls(tmp_path: Path) -> None:
    cache = TvmazeCatalogCache(tmp_path / "cache")
    source = (SourceEpisodeInput("one.mkv", ParseResult(season=1, episodes=(1,))),)
    cold_getter = CountingGetter()
    cold = assign_episode_group(_show(), source, cache, cold_getter)

    warm_getter = CountingGetter()
    warm = assign_episode_group(_show(), source, cache, warm_getter)

    assert cold == warm
    assert len(cold_getter.calls) == 1
    assert warm_getter.calls == []
