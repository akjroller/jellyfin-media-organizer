from __future__ import annotations

import pytest

from jellyfin_show_organizer.filename_parser import parse_video_path
from jellyfin_show_organizer.models import ParseResult

pytestmark = pytest.mark.local


def test_parent_matching_episode_confirms_one_prepended_release_token() -> None:
    parsed = parse_video_path(
        "synthetic/Example.Series.S01E03.Release/TEAM-Example.Series.S01E03.720p.mkv"
    )

    assert parsed == ParseResult(
        series_hint="Example Series",
        season=1,
        episodes=(3,),
    )


def test_legitimate_hyphenated_series_is_not_stripped() -> None:
    parsed = parse_video_path(
        "synthetic/Spider-Man.S01E03.Release/Spider-Man.S01E03.720p.mkv"
    )

    assert parsed == ParseResult(
        series_hint="Spider-Man",
        season=1,
        episodes=(3,),
    )


def test_parent_must_exactly_match_the_remainder_after_release_token() -> None:
    parsed = parse_video_path(
        "synthetic/Example.Series.S01E03.Release/TEAM-Wrong.Series.S01E03.720p.mkv"
    )

    assert parsed == ParseResult(
        series_hint="TEAM-Wrong Series",
        season=1,
        episodes=(3,),
    )


def test_generic_season_parent_cannot_confirm_release_token_stripping() -> None:
    parsed = parse_video_path(
        "synthetic/Example Series/Season 01/TEAM-Example.Series.S01E03.720p.mkv"
    )

    assert parsed == ParseResult(
        series_hint="TEAM-Example Series",
        season=1,
        episodes=(3,),
    )


def test_parent_coordinate_conflict_blocks_release_token_stripping() -> None:
    parsed = parse_video_path(
        "synthetic/Example.Series.S01E04.Release/TEAM-Example.Series.S01E03.720p.mkv"
    )

    assert parsed == ParseResult(
        series_hint="TEAM-Example Series",
        season=1,
        episodes=(3,),
    )


def test_parent_year_conflict_blocks_release_token_stripping() -> None:
    parsed = parse_video_path(
        "synthetic/Example.Series.2024.S01E03.Release/"
        "TEAM-Example.Series.2023.S01E03.720p.mkv"
    )

    assert parsed == ParseResult(
        series_hint="TEAM-Example Series",
        season=1,
        episodes=(3,),
        year=2023,
    )
