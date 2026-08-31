from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jellyfin_show_organizer.destination import (
    DestinationStatus,
    build_episode_destination,
)
from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group,
)
from jellyfin_show_organizer.extra_classifier import (
    ExtraDisposition,
    classify_extra,
)
from jellyfin_show_organizer.filename_parser import parse_video_path
from jellyfin_show_organizer.models import CanonicalShow, NumberingMode, ParseResult
from jellyfin_show_organizer.tvmaze_cache import TvmazeCatalogCache

pytestmark = pytest.mark.local

CATALOG = [
    {
        "id": 9001,
        "season": 0,
        "number": 1,
        "name": "First OVA",
        "airdate": "2023-12-01",
    },
    {
        "id": 9002,
        "season": 0,
        "number": 2,
        "name": "Second OVA",
        "airdate": "2023-12-08",
    },
    {
        "id": 1001,
        "season": 1,
        "number": 1,
        "name": "New Year",
        "airdate": "2024-01-10",
    },
    {
        "id": 1002,
        "season": 1,
        "number": 2,
        "name": "Leap Signal",
        "airdate": "2024-02-29",
    },
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


def _show(mode: NumberingMode) -> CanonicalShow:
    return CanonicalShow(
        source_key="Fabricated Series",
        tvmaze_id=4242,
        title="Fabricated Series",
        year=2024,
        numbering_mode=mode,
    )


@pytest.mark.parametrize(
    ("path", "kind", "number"),
    [
        ("Fabricated Series/Fabricated.Series.OVA.02.-.Side.Story.mkv", "ova", 2),
        ("Fabricated Series/Fabricated Series OAD-3 [1080p].mkv", "oad", 3),
    ],
)
def test_parser_preserves_structural_special_numbering(
    path: str,
    kind: str,
    number: int,
) -> None:
    parsed = parse_video_path(path)

    assert parsed.series_hint == "Fabricated Series"
    assert parsed.special_kind == kind
    assert parsed.special_number == number
    assert parsed.absolute_episode is None
    assert classify_extra(path).disposition is ExtraDisposition.EPISODE_CANDIDATE


def test_parser_preserves_unambiguous_airdate_without_treating_year_as_episode() -> None:
    parsed = parse_video_path(
        "Fabricated Series/Fabricated.Series.2024-02-29.1080p.mkv"
    )

    assert parsed.series_hint == "Fabricated Series"
    assert parsed.airdate == "2024-02-29"
    assert parsed.absolute_episode is None
    assert parsed.episodes == ()


def test_ordinary_special_title_word_does_not_create_special_numbering() -> None:
    parsed = parse_video_path(
        "Fabricated Series/Fabricated.Series.S01E01.Special.Delivery.mkv"
    )

    assert parsed.season == 1
    assert parsed.episodes == (1,)
    assert parsed.special_kind is None
    assert parsed.special_number is None


def test_special_mode_maps_numbered_ova_to_unique_season_zero_episode(
    tmp_path: Path,
) -> None:
    parsed = parse_video_path(
        "Fabricated Series/Fabricated.Series.OVA.02.-.Side.Story.mkv"
    )
    result = assign_episode_group(
        _show(NumberingMode.SPECIAL),
        (SourceEpisodeInput("ova-two.mkv", parsed),),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assert result.status is AssignmentStatus.MATCHED
    assignment = result.assignments[0]
    assert assignment.episodes[0].tvmaze_episode_id == 9002
    assert "numbering-mode:special" in assignment.evidence.reasons
    assert "special-kind:ova" in assignment.evidence.reasons

    destination = build_episode_destination(
        result.show,
        assignment,
        ".mkv",
    )
    assert destination.status is DestinationStatus.READY
    assert destination.relative_path is not None
    assert "/Season 00/" in destination.relative_path
    assert "S00E02" in destination.relative_path


def test_missing_special_catalog_coordinate_remains_unresolved(tmp_path: Path) -> None:
    result = assign_episode_group(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "oad-three.mkv",
                ParseResult(special_kind="oad", special_number=3),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.UNRESOLVED
    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert "missing-special-catalog-entry:oad:3" in assignment.evidence.reasons


def test_airdate_mode_maps_only_unique_provider_date(tmp_path: Path) -> None:
    result = assign_episode_group(
        _show(NumberingMode.AIRDATE),
        (
            SourceEpisodeInput(
                "leap-day.mkv",
                ParseResult(airdate="2024-02-29"),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 1002
    assert "airdate-match:2024-02-29->S01E02" in assignment.evidence.reasons

    destination = build_episode_destination(result.show, assignment, ".mp4")
    assert destination.status is DestinationStatus.READY
    assert destination.relative_path is not None
    assert "S01E02" in destination.relative_path


def test_duplicate_provider_airdate_fails_closed_as_suspicious(tmp_path: Path) -> None:
    duplicate_date_catalog = [
        {
            "id": 1001,
            "season": 1,
            "number": 1,
            "name": "Alpha",
            "airdate": "2024-01-10",
        },
        {
            "id": 1002,
            "season": 1,
            "number": 2,
            "name": "Beta",
            "airdate": "2024-01-10",
        },
    ]
    result = assign_episode_group(
        _show(NumberingMode.AIRDATE),
        (SourceEpisodeInput("dated.mkv", ParseResult(airdate="2024-01-10")),),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(duplicate_date_catalog),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.SUSPICIOUS
    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert assignment.episodes == ()
    assert "ambiguous-airdate-catalog-entry:2024-01-10" in assignment.evidence.reasons


def test_missing_provider_airdate_remains_unresolved(tmp_path: Path) -> None:
    result = assign_episode_group(
        _show(NumberingMode.AIRDATE),
        (SourceEpisodeInput("dated.mkv", ParseResult(airdate="2025-01-01")),),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(),
    )

    assert result.status is AssignmentStatus.UNRESOLVED
    assert (
        "missing-airdate-catalog-entry:2025-01-01"
        in result.assignments[0].evidence.reasons
    )


def test_mixed_airdate_and_aired_evidence_stops_before_provider_access(
    tmp_path: Path,
) -> None:
    getter = CountingGetter()
    parsed = parse_video_path(
        "Fabricated Series/Fabricated.Series.S01E01.2024-01-10.mkv"
    )
    result = assign_episode_group(
        _show(NumberingMode.AIRDATE),
        (SourceEpisodeInput("mixed.mkv", parsed),),
        TvmazeCatalogCache(tmp_path / "cache"),
        getter,
    )

    assert result.status is AssignmentStatus.SUSPICIOUS
    assert not getter.calls
    assert "mixed-numbering-evidence:conflict" in result.assignments[0].evidence.reasons


def test_special_and_airdate_modes_are_valid_override_numbering_values() -> None:
    assert NumberingMode("special") is NumberingMode.SPECIAL
    assert NumberingMode("airdate") is NumberingMode.AIRDATE
