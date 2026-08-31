import pytest

from jellyfin_show_organizer.filename_parser import parse_video_path
from jellyfin_show_organizer.models import ParseResult

pytestmark = pytest.mark.local


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        (
            "synthetic/Mirror City/Mirror.City.2005.S01E01.First.Light.1080p-SYNTH.mkv",
            ParseResult(
                series_hint="Mirror City",
                season=1,
                episodes=(1,),
                year=2005,
                title_hint="First Light",
            ),
        ),
        (
            "synthetic/River Patrol/River.Patrol.S01E03-E04.Double.Current.mkv",
            ParseResult(
                series_hint="River Patrol",
                season=1,
                episodes=(3, 4),
                title_hint="Double Current",
            ),
        ),
        (
            "synthetic/Signal Ridge/Signal.Ridge.S02E07E08.Twin.Beacons.mp4",
            ParseResult(
                series_hint="Signal Ridge",
                season=2,
                episodes=(7, 8),
                title_hint="Twin Beacons",
            ),
        ),
        (
            "synthetic/Old Harbor/Old.Harbor.3x05.Low.Tide.avi",
            ParseResult(
                series_hint="Old Harbor",
                season=3,
                episodes=(5,),
                title_hint="Low Tide",
            ),
        ),
        (
            "synthetic/Moonblade/[SYNTH] Moonblade (135) [1080p].mkv",
            ParseResult(series_hint="Moonblade", absolute_episode=135),
        ),
        (
            "synthetic/Starforge Academy/[SYNTH] Starforge Academy - 027 [1080p].mkv",
            ParseResult(series_hint="Starforge Academy", absolute_episode=27),
        ),
        (
            "synthetic/Bubble Borough/Bubble.Borough.Episode.01a.Red.Kite.mkv",
            ParseResult(
                series_hint="Bubble Borough",
                absolute_episode=1,
                segment_hint="a",
                title_hint="Red Kite",
            ),
        ),
    ],
)
def test_parser_handles_supported_numbering_grammars(
    relative_path: str,
    expected: ParseResult,
):
    assert parse_video_path(relative_path) == expected


def test_parser_extracts_embedded_id_year_and_title_without_provider_calls():
    parsed = parse_video_path(
        "synthetic/Northstar Files/"
        "Northstar.Files.2020.S03E05.The.Signal.tvmaze-12345.1080p-SYNTH.mkv"
    )

    assert parsed == ParseResult(
        series_hint="Northstar Files",
        season=3,
        episodes=(5,),
        year=2020,
        embedded_tvmaze_id=12345,
        title_hint="The Signal",
    )


def test_explicit_filename_hint_wins_over_noisy_parent_folder():
    parsed = parse_video_path(
        "synthetic/Complete.Collection.S01-S09/Harbor.Watch.S04E02.Crosswind.mkv"
    )

    assert parsed.series_hint == "Harbor Watch"
    assert parsed.season == 4
    assert parsed.episodes == (2,)
    assert parsed.title_hint == "Crosswind"


def test_parent_folder_is_only_a_fallback_when_filename_has_no_series_prefix():
    parsed = parse_video_path(
        "synthetic/Bubble Borough (1999)/Episode 01b Blue Boat.mkv"
    )

    assert parsed == ParseResult(
        series_hint="Bubble Borough",
        absolute_episode=1,
        segment_hint="b",
        year=1999,
        title_hint="Blue Boat",
    )


def test_parser_is_deterministic_and_does_not_require_filesystem_state():
    path = "synthetic/River Patrol/River.Patrol.S01E03-E04.Double.Current.mkv"

    assert parse_video_path(path) == parse_video_path(path)
