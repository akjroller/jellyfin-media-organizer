from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group,
)
from jellyfin_show_organizer.models import CanonicalShow, NumberingMode, ParseResult
from jellyfin_show_organizer.tvmaze_cache import TvmazeCatalogCache


class StaticGetter:
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


def _catalog_with_bad_optional_metadata() -> list[dict[str, object]]:
    return [
        {
            "id": 1001,
            "season": 1,
            "number": 1,
            "name": "Part Alpha",
            "airdate": "2024-01-01",
            "type": "regular",
        },
        {
            "id": 1002,
            "season": 1,
            "number": 2,
            "name": "Part Beta",
            "airdate": "not-a-date",
            "type": "regular",
        },
    ]


def test_aired_assignment_uses_row_with_malformed_irrelevant_airdate(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(NumberingMode.AIRED),
        (SourceEpisodeInput("two.mkv", ParseResult(season=1, episodes=(2,))),),
        TvmazeCatalogCache(tmp_path / "cache"),
        StaticGetter(_catalog_with_bad_optional_metadata()),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 1002
    assert assignment.episodes[0].airdate is None
    assert "catalog-diagnostic:invalid-catalog-airdate:1" in assignment.evidence.reasons


def test_absolute_assignment_is_not_blocked_by_malformed_airdate(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(NumberingMode.ABSOLUTE),
        (SourceEpisodeInput("two.mkv", ParseResult(absolute_episode=2)),),
        TvmazeCatalogCache(tmp_path / "cache"),
        StaticGetter(_catalog_with_bad_optional_metadata()),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 1002
    assert "catalog-diagnostic:invalid-catalog-airdate:1" in assignment.evidence.reasons


def test_date_assignment_can_use_valid_date_despite_unrelated_bad_airdate(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(NumberingMode.DATE),
        (
            SourceEpisodeInput("dated.mkv", ParseResult(episode_date="2024-01-01")),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        StaticGetter(_catalog_with_bad_optional_metadata()),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 1001
    assert "catalog-diagnostic:invalid-catalog-airdate:1" in assignment.evidence.reasons


def test_date_assignment_fails_closed_when_required_date_is_unavailable(
    tmp_path: Path,
) -> None:
    result = assign_episode_group(
        _show(NumberingMode.DATE),
        (
            SourceEpisodeInput("dated.mkv", ParseResult(episode_date="2024-01-02")),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        StaticGetter(_catalog_with_bad_optional_metadata()),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.UNRESOLVED
    assert assignment.episodes == ()
    assert "missing-date-catalog-entry:2024-01-02" in assignment.evidence.reasons
    assert "catalog-diagnostic:invalid-catalog-airdate:1" in assignment.evidence.reasons


def test_special_assignment_uses_season_zero_when_type_is_malformed(
    tmp_path: Path,
) -> None:
    catalog = [
        {
            "id": 9001,
            "season": 0,
            "number": 1,
            "name": "OVA One",
            "type": [],
        }
    ]
    result = assign_episode_group(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "ova.mkv",
                ParseResult(special_kind="ova", special_episode=1),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        StaticGetter(catalog),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 9001
    assert assignment.episodes[0].episode_type is None
    assert "catalog-diagnostic:invalid-catalog-type:0" in assignment.evidence.reasons


def test_special_assignment_does_not_guess_through_malformed_required_type(
    tmp_path: Path,
) -> None:
    catalog = [
        {
            "id": 9001,
            "season": 1,
            "number": 1,
            "name": "OVA One",
            "type": [],
        }
    ]
    result = assign_episode_group(
        _show(NumberingMode.SPECIAL),
        (
            SourceEpisodeInput(
                "ova.mkv",
                ParseResult(special_kind="ova", special_episode=1),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        StaticGetter(catalog),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.UNRESOLVED
    assert assignment.episodes == ()
    assert "missing-special-catalog-entry:1" in assignment.evidence.reasons
    assert "catalog-diagnostic:invalid-catalog-type:0" in assignment.evidence.reasons


def test_structural_catalog_corruption_remains_blocking(tmp_path: Path) -> None:
    catalog = [
        {"id": 1001, "season": 1, "number": 1, "name": "Part Alpha"},
        {"id": "bad", "season": 2, "number": 1, "name": "Broken"},
    ]
    result = assign_episode_group(
        _show(NumberingMode.AIRED),
        (SourceEpisodeInput("one.mkv", ParseResult(season=1, episodes=(1,))),),
        TvmazeCatalogCache(tmp_path / "cache"),
        StaticGetter(catalog),
    )

    assignment = result.assignments[0]
    assert result.status is AssignmentStatus.UNRESOLVED
    assert assignment.episodes == ()
    assert "invalid-catalog-episode-id:1" in assignment.evidence.reasons
