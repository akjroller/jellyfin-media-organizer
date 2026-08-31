from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.destination import build_episode_destination
from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group,
)
from jellyfin_show_organizer.filename_parser import parse_video_path
from jellyfin_show_organizer.models import CanonicalShow, NumberingMode, ParseResult
from jellyfin_show_organizer.tvmaze_cache import TvmazeCatalogCache

pytestmark = pytest.mark.local


class CountingGetter:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        return self.response


def _show(mode: NumberingMode) -> CanonicalShow:
    return CanonicalShow(
        source_key="Example Series",
        tvmaze_id=4242,
        title="Example Series",
        year=2024,
        numbering_mode=mode,
    )


def test_parser_preserves_ova_and_oad_numbering_evidence() -> None:
    ova = parse_video_path(
        "synthetic/Example Series/Example Series OVA 02 - Moonlight.mkv"
    )
    oad = parse_video_path(
        "synthetic/Example Series/Example Series OAD01 - Harbor Day.mkv"
    )

    assert ova == ParseResult(
        series_hint="Example Series",
        special_kind="ova",
        special_episode=2,
        title_hint="Moonlight",
    )
    assert oad == ParseResult(
        series_hint="Example Series",
        special_kind="oad",
        special_episode=1,
        title_hint="Harbor Day",
    )


def test_parser_preserves_episode_date_without_confusing_show_year() -> None:
    parsed = parse_video_path(
        "synthetic/Mirror City (2005)/Mirror City (2005) 2024-03-14 Broadcast.mkv"
    )

    assert parsed == ParseResult(
        series_hint="Mirror City",
        episode_date="2024-03-14",
        year=2005,
        title_hint="Broadcast",
    )


def test_invalid_calendar_date_is_not_promoted_to_date_evidence() -> None:
    parsed = parse_video_path(
        "synthetic/Mirror City/Mirror City 2024-02-31 Broadcast.mkv"
    )

    assert parsed.episode_date is None
    assert parsed.special_kind is None


def test_title_word_special_does_not_change_numbering_policy() -> None:
    parsed = parse_video_path(
        "synthetic/Mirror City/Mirror City S01E02 Special Delivery.mkv"
    )

    assert parsed.season == 1
    assert parsed.episodes == (2,)
    assert parsed.title_hint == "Special Delivery"
    assert parsed.special_kind is None
    assert parsed.special_episode is None


def test_unique_special_provider_evidence_maps_ova(tmp_path: Path) -> None:
    getter = CountingGetter(
        [
            {
                "id": 9001,
                "season": 0,
                "number": 1,
                "name": "Bonus Flight",
                "type": "significant_special",
                "airdate": "2024-01-02",
            }
        ]
    )
    result = assign_episode_group(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "ova.mkv",
                ParseResult(special_kind="ova", special_episode=1),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 9001
    assert "numbering-mode:special" in assignment.evidence.reasons
    assert "special-kind:ova" in assignment.evidence.reasons

    destination = build_episode_destination(
        _show(NumberingMode.SPECIAL), assignment, ".mkv"
    )
    assert destination.relative_path is not None
    assert "/Season 00/" in destination.relative_path
    assert "numbering-mode:special" in destination.reasons


def test_ambiguous_special_provider_evidence_fails_closed(tmp_path: Path) -> None:
    getter = CountingGetter(
        [
            {
                "id": 9001,
                "season": 0,
                "number": 1,
                "name": "Bonus Alpha",
                "type": "significant_special",
            },
            {
                "id": 9101,
                "season": 9,
                "number": 1,
                "name": "Bonus Beta",
                "type": "significant_special",
            },
        ]
    )
    result = assign_episode_group(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "ova.mkv",
                ParseResult(special_kind="ova", special_episode=1),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert "ambiguous-special-catalog-entry:1" in result.assignments[0].evidence.reasons


def test_kind_labeled_special_breaks_otherwise_ambiguous_tie(tmp_path: Path) -> None:
    getter = CountingGetter(
        [
            {
                "id": 9001,
                "season": 0,
                "number": 1,
                "name": "OVA - Bonus Alpha",
                "type": "significant_special",
            },
            {
                "id": 9101,
                "season": 9,
                "number": 1,
                "name": "OAD - Bonus Beta",
                "type": "significant_special",
            },
        ]
    )
    result = assign_episode_group(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "ova.mkv",
                ParseResult(special_kind="ova", special_episode=1),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.MATCHED
    assert result.assignments[0].episodes[0].tvmaze_episode_id == 9001


def test_unique_airdate_maps_date_numbering(tmp_path: Path) -> None:
    getter = CountingGetter(
        [
            {
                "id": 1005,
                "season": 1,
                "number": 5,
                "name": "Broadcast",
                "airdate": "2024-03-14",
            }
        ]
    )
    result = assign_episode_group(
        _show(NumberingMode.DATE),
        (
            SourceEpisodeInput(
                "broadcast.mkv",
                ParseResult(episode_date="2024-03-14"),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 1005
    assert "numbering-mode:date" in assignment.evidence.reasons
    assert "date-match:2024-03-14->S01E05" in assignment.evidence.reasons


def test_duplicate_airdate_remains_suspicious(tmp_path: Path) -> None:
    getter = CountingGetter(
        [
            {
                "id": 1005,
                "season": 1,
                "number": 5,
                "name": "First",
                "airdate": "2024-03-14",
            },
            {
                "id": 2001,
                "season": 2,
                "number": 1,
                "name": "Second",
                "airdate": "2024-03-14",
            },
        ]
    )
    result = assign_episode_group(
        _show(NumberingMode.DATE),
        (
            SourceEpisodeInput(
                "broadcast.mkv",
                ParseResult(episode_date="2024-03-14"),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert (
        "ambiguous-date-catalog-entry:2024-03-14"
        in result.assignments[0].evidence.reasons
    )


def test_missing_airdate_remains_unresolved(tmp_path: Path) -> None:
    getter = CountingGetter(
        [
            {
                "id": 1005,
                "season": 1,
                "number": 5,
                "name": "Other Day",
                "airdate": "2024-03-15",
            }
        ]
    )
    result = assign_episode_group(
        _show(NumberingMode.DATE),
        (
            SourceEpisodeInput(
                "broadcast.mkv",
                ParseResult(episode_date="2024-03-14"),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.UNRESOLVED
    assert "missing-date-catalog-entry:2024-03-14" in (
        result.assignments[0].evidence.reasons
    )


def test_group_cannot_mix_date_and_aired_numbering(tmp_path: Path) -> None:
    getter = CountingGetter([])
    result = assign_episode_group(
        _show(NumberingMode.DATE),
        (
            SourceEpisodeInput("aired.mkv", ParseResult(season=1, episodes=(1,))),
            SourceEpisodeInput("dated.mkv", ParseResult(episode_date="2024-03-14")),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert getter.calls == []
    assert "mixed-numbering-evidence:aired,date" in (
        result.assignments[0].evidence.reasons
    )


def test_numbering_policy_conflict_stops_before_provider_access(tmp_path: Path) -> None:
    getter = CountingGetter([])
    result = assign_episode_group(
        _show(NumberingMode.AIRED),
        (
            SourceEpisodeInput(
                "ova.mkv", ParseResult(special_kind="ova", special_episode=1)
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert getter.calls == []
    assert (
        result.assignments[0]
        .evidence.reasons[0]
        .startswith("numbering-policy-conflict:expected-aired:observed-special")
    )
