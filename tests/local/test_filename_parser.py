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


def test_parser_handles_version_suffixed_absolute_episode():
    parsed = parse_video_path(
        "synthetic/Revision Rangers/Revision Rangers - 01v2 [1080p].mkv"
    )

    assert parsed == ParseResult(
        series_hint="Revision Rangers",
        absolute_episode=1,
    )


def test_parser_handles_guarded_bare_absolute_episode():
    parsed = parse_video_path(
        "synthetic/Orbit Quest (2024)/Orbit Quest (2024) 068 (1080p).mkv"
    )

    assert parsed == ParseResult(
        series_hint="Orbit Quest",
        absolute_episode=68,
        year=2024,
    )


def test_parser_handles_spaced_season_episode_tokens():
    parsed = parse_video_path(
        "synthetic/Fair Weather/Season 2/Fair Weather S02 E03.avi"
    )

    assert parsed == ParseResult(
        series_hint="Fair Weather",
        season=2,
        episodes=(3,),
    )


def test_parser_handles_episode_token_embedded_in_release_name():
    parsed = parse_video_path(
        "synthetic/House Calls/House.Calls.S07.1080p/release-houses07e02-1080p.mkv"
    )

    assert parsed == ParseResult(
        series_hint="House Calls",
        season=7,
        episodes=(2,),
    )


def test_parser_handles_legacy_bracketed_segment_notation():
    parsed = parse_video_path(
        "synthetic/Bubble Borough/Bubble Borough [season 01][episod 04a] - Red Kite.avi"
    )

    assert parsed == ParseResult(
        series_hint="Bubble Borough",
        season=1,
        episodes=(4,),
        segment_hint="a",
        title_hint="Red Kite",
    )


def test_parser_handles_bare_absolute_with_matching_parent_series():
    parsed = parse_video_path(
        "synthetic/Mnemonic Garden/Mnemonic Garden 02 Green Door.mkv"
    )

    assert parsed == ParseResult(
        series_hint="Mnemonic Garden",
        absolute_episode=2,
        title_hint="Green Door",
    )


def test_parser_recovers_single_unambiguous_ancestor_episode_context():
    parsed = parse_video_path(
        "synthetic/Titan School/Titan.School.S02E03.INTERNAL/release203.avi"
    )

    assert parsed == ParseResult(
        series_hint="Titan School",
        season=2,
        episodes=(3,),
    )


def test_embedded_episode_token_does_not_consume_resolution_as_episode_range():
    parsed = parse_video_path(
        "synthetic/House Calls/House.Calls.S07.1080p/release-houses07e02-1080p.mkv"
    )

    assert parsed.season == 7
    assert parsed.episodes == (2,)


def test_conflicting_ancestor_episode_context_fails_closed():
    parsed = parse_video_path(
        "synthetic/Conflict.Show.S01E01/Conflict.Show.S02E02/release.mkv"
    )

    assert parsed.season is None
    assert parsed.episodes == ()
    assert parsed.absolute_episode is None


def test_ambiguous_compact_number_is_not_guessed_as_season_episode():
    parsed = parse_video_path("synthetic/Signal Files/Signal.Files.406.Tabula.Rasa.mkv")

    assert parsed.series_hint == "Signal Files"
    assert parsed.season is None
    assert parsed.episodes == ()
    assert parsed.absolute_episode is None


def test_whole_season_release_without_episode_identity_remains_unresolved():
    parsed = parse_video_path(
        "synthetic/Stranger Harbor/Stranger.Harbor.S03.2160p.WEB-DL.mkv"
    )

    assert parsed.series_hint == "Stranger Harbor"
    assert parsed.season is None
    assert parsed.episodes == ()
    assert parsed.absolute_episode is None


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
