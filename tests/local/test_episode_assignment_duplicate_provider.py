from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group,
)
from jellyfin_show_organizer.models import CanonicalShow, NumberingMode, ParseResult
from jellyfin_show_organizer.tvmaze_cache import TvmazeCatalogCache

pytestmark = pytest.mark.local

CATALOG = [
    {"id": 1001, "season": 1, "number": 1, "name": "First Signal"},
    {"id": 1002, "season": 1, "number": 2, "name": "Second Signal"},
    {"id": 2001, "season": 2, "number": 1, "name": "Return Signal"},
]


class CountingGetter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        return CATALOG


def _show(mode: NumberingMode = NumberingMode.AIRED) -> CanonicalShow:
    return CanonicalShow(
        source_key="Fabricated Series",
        tvmaze_id=4242,
        title="Fabricated Series",
        year=2024,
        numbering_mode=mode,
    )


def test_duplicate_aired_provider_episode_marks_all_claimants_suspicious(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(),
        (
            SourceEpisodeInput("copy-b.mkv", ParseResult(season=1, episodes=(1,))),
            SourceEpisodeInput("copy-a.mkv", ParseResult(season=1, episodes=(1,))),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert [assignment.source_key for assignment in result.assignments] == [
        "copy-a.mkv",
        "copy-b.mkv",
    ]
    assert all(
        assignment.status is AssignmentStatus.SUSPICIOUS
        for assignment in result.assignments
    )
    assert all(assignment.episodes == () for assignment in result.assignments)
    assert all(
        "duplicate-provider-episode-assignment:tvmaze-episode:1001"
        in assignment.evidence.reasons
        for assignment in result.assignments
    )
    assert len(getter.calls) == 1


def test_duplicate_absolute_provider_episode_is_not_silently_matched(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(NumberingMode.ABSOLUTE),
        (
            SourceEpisodeInput("absolute-one.mkv", ParseResult(absolute_episode=2)),
            SourceEpisodeInput("absolute-two.mkv", ParseResult(absolute_episode=2)),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert all(assignment.episodes == () for assignment in result.assignments)
    assert all(
        "duplicate-provider-episode-assignment:tvmaze-episode:1002"
        in assignment.evidence.reasons
        for assignment in result.assignments
    )


def test_duplicate_detection_is_deterministic_across_input_order(
    tmp_path: Path,
) -> None:
    sources = (
        SourceEpisodeInput("z-copy.mkv", ParseResult(season=1, episodes=(1,))),
        SourceEpisodeInput("a-copy.mkv", ParseResult(season=1, episodes=(1,))),
    )

    first = assign_episode_group(
        _show(),
        sources,
        TvmazeCatalogCache(tmp_path / "cache-first"),
        CountingGetter(),
    )
    second = assign_episode_group(
        _show(),
        tuple(reversed(sources)),
        TvmazeCatalogCache(tmp_path / "cache-second"),
        CountingGetter(),
    )

    assert first == second


def test_one_multi_episode_source_keeps_intentional_multiple_mappings(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(),
        (
            SourceEpisodeInput(
                "double-episode.mkv",
                ParseResult(season=1, episodes=(1, 2)),
            ),
            SourceEpisodeInput(
                "later-episode.mkv",
                ParseResult(season=2, episodes=(1,)),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assert result.status is AssignmentStatus.MATCHED
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    assert [
        episode.tvmaze_episode_id
        for episode in by_source["double-episode.mkv"].episodes
    ] == [1001, 1002]
    assert by_source["later-episode.mkv"].episodes[0].tvmaze_episode_id == 2001
