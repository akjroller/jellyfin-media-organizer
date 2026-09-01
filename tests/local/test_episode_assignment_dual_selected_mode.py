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
    {"id": 1001, "season": 1, "number": 1, "name": "Alpha"},
    {"id": 1002, "season": 1, "number": 2, "name": "Beta"},
    {"id": 2001, "season": 2, "number": 1, "name": "Return"},
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


def _show(mode: NumberingMode) -> CanonicalShow:
    return CanonicalShow(
        source_key="Synthetic Series",
        tvmaze_id=4242,
        title="Synthetic Series",
        year=2024,
        numbering_mode=mode,
    )


def test_absolute_selected_mode_accepts_complete_dual_evidence(tmp_path: Path) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(NumberingMode.ABSOLUTE),
        (
            SourceEpisodeInput(
                "dual.mkv",
                ParseResult(season=9, episodes=(24,), absolute_episode=2),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.MATCHED
    assignment = result.assignments[0]
    assert assignment.episodes[0].tvmaze_episode_id == 1002
    assert "numbering-mode:absolute" in assignment.evidence.reasons
    assert (
        "dual-numbering-evidence:secondary-aired:S09E24"
        in assignment.evidence.reasons
    )
    assert len(getter.calls) == 1


def test_aired_selected_mode_accepts_complete_dual_evidence(tmp_path: Path) -> None:
    result = assign_episode_group(
        _show(NumberingMode.AIRED),
        (
            SourceEpisodeInput(
                "dual.mkv",
                ParseResult(season=1, episodes=(1,), absolute_episode=99),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assert result.status is AssignmentStatus.MATCHED
    assignment = result.assignments[0]
    assert assignment.episodes[0].tvmaze_episode_id == 1001
    assert "numbering-mode:aired" in assignment.evidence.reasons
    assert "dual-numbering-evidence:secondary-absolute:99" in assignment.evidence.reasons


def test_dual_evidence_remains_conflicting_for_unrelated_selected_mode(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "dual.mkv",
                ParseResult(season=1, episodes=(1,), absolute_episode=1),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert getter.calls == []
    assert "mixed-numbering-evidence:conflict" in result.assignments[0].evidence.reasons


def test_incomplete_secondary_aired_evidence_still_fails_closed(tmp_path: Path) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(NumberingMode.ABSOLUTE),
        (
            SourceEpisodeInput(
                "incomplete.mkv",
                ParseResult(season=3, absolute_episode=2),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert getter.calls == []
    assert "mixed-numbering-evidence:conflict" in result.assignments[0].evidence.reasons


def test_separate_aired_and_absolute_sources_are_still_mixed(tmp_path: Path) -> None:
    getter = CountingGetter()
    result = assign_episode_group(
        _show(NumberingMode.AIRED),
        (
            SourceEpisodeInput("aired.mkv", ParseResult(season=1, episodes=(1,))),
            SourceEpisodeInput("absolute.mkv", ParseResult(absolute_episode=2)),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert getter.calls == []
    assert (
        "mixed-numbering-evidence:absolute,aired"
        in result.assignments[0].evidence.reasons
    )
