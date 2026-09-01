from __future__ import annotations

import pytest

from jellyfin_show_organizer.destination import (
    DestinationStatus,
    build_extra_destination,
    find_destination_collisions,
)
from jellyfin_show_organizer.extra_classifier import ExtraDisposition, classify_extra
from jellyfin_show_organizer.extra_naming import derive_extra_display_identity
from jellyfin_show_organizer.models import (
    CanonicalShow,
    ExtraDecision,
    NumberingMode,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.planner import _logical_identity

pytestmark = pytest.mark.local


def _show() -> CanonicalShow:
    return CanonicalShow(
        source_key="fixture-series",
        tvmaze_id=4242,
        title="Fixture Series",
        year=2026,
        numbering_mode=NumberingMode.AIRED,
    )


def _extra_record(relative_path: str, kind: str = "extra") -> PlanRecord:
    return PlanRecord(
        source=SourceFile(
            relative_path=relative_path,
            extension=".mkv",
            fingerprint=SourceFingerprint(size=100, mtime_ns=1),
        ),
        status=TerminalStatus.EXTRA,
        parse=ParseResult(series_hint="Fixture Series"),
        show=_show(),
        destination="placeholder.mkv",
        extra=ExtraDecision(kind=kind, rule="fabricated test evidence"),
    )


def _destination(relative_path: str, kind: str = "extra"):
    return build_extra_destination(
        _show(),
        source_key=relative_path,
        extra=ExtraDecision(kind=kind, rule="fabricated test evidence"),
        source_extension=".mkv",
    )


def test_distinct_generic_extra_titles_receive_distinct_destinations() -> None:
    drawing = _destination(
        "Fixture Series/Fixture.Series.S01EXTRA.How.To.Draw.Character.mkv"
    )
    music = _destination("Fixture Series/Fixture.Series.S01EXTRA.Music.Video.mkv")

    assert drawing.status is DestinationStatus.READY
    assert music.status is DestinationStatus.READY
    assert drawing.relative_path == (
        "Fixture Series (2026)/extras/How To Draw Character.mkv"
    )
    assert music.relative_path == "Fixture Series (2026)/extras/Music Video.mkv"
    assert drawing.relative_path != music.relative_path
    assert find_destination_collisions((drawing, music)) == ()
    assert "extra-display-title:How To Draw Character" in drawing.reasons


@pytest.mark.parametrize(
    ("relative_path", "kind", "expected_name"),
    [
        (
            "Fixture Series/Fixture.Series.NCOP.01.1080p.WEB-DL.x265-GROUP.mkv",
            "creditless-opening",
            "Creditless Opening 01.mkv",
        ),
        (
            "Fixture Series/Fixture.Series.NCOP.02.720p.WEB-DL.x264-GROUP.mkv",
            "creditless-opening",
            "Creditless Opening 02.mkv",
        ),
        (
            "Fixture Series/Fixture.Series.NCED.01.1080p.WEB-DL.x265-GROUP.mkv",
            "creditless-ending",
            "Creditless Ending 01.mkv",
        ),
        (
            "Fixture Series/Fixture.Series.NCED.02.720p.WEB-DL.x264-GROUP.mkv",
            "creditless-ending",
            "Creditless Ending 02.mkv",
        ),
    ],
)
def test_numbered_creditless_variants_preserve_sequence_identity(
    relative_path: str,
    kind: str,
    expected_name: str,
) -> None:
    decision = _destination(relative_path, kind)

    assert decision.status is DestinationStatus.READY
    assert decision.relative_path is not None
    assert decision.relative_path.endswith(expected_name)


def test_numbered_creditless_markers_classify_as_extras_not_absolute_episodes() -> None:
    result = classify_extra(
        "Fixture Series/Fixture.Series.NCOP.01.1080p.WEB-DL.x265-GROUP.mkv"
    )

    assert result.disposition is ExtraDisposition.EXTRA
    assert result.decision is not None
    assert result.decision.kind == "creditless-opening"


def test_quality_and_codec_release_tails_do_not_change_semantic_extra_identity() -> (
    None
):
    high = "Fixture Series/Fixture.Series.S01EXTRA.Cast.Reunion.1080p.WEB-DL.x265-GROUP.mkv"
    low = (
        "Fixture Series/Fixture.Series.S01EXTRA.Cast.Reunion.720p.WEB-DL.x264-OTHER.mkv"
    )

    high_naming = derive_extra_display_identity(
        high, "extra", show_title="Fixture Series"
    )
    low_naming = derive_extra_display_identity(
        low, "extra", show_title="Fixture Series"
    )
    high_destination = _destination(high)
    low_destination = _destination(low)

    assert high_naming.display_title == "Cast Reunion"
    assert low_naming.display_title == "Cast Reunion"
    assert high_naming.identity_key == low_naming.identity_key
    assert high_destination.relative_path == low_destination.relative_path
    assert "extra-naming-release-noise-trimmed" in high_naming.reasons


def test_true_alternate_extra_copies_converge_to_one_logical_identity() -> None:
    high = _extra_record(
        "Fixture Series/Fixture.Series.S01EXTRA.Cast.Reunion.1080p.WEB-DL.x265-GROUP.mkv"
    )
    low = _extra_record(
        "Fixture Series/Fixture.Series.S01EXTRA.Cast.Reunion.720p.WEB-DL.x264-OTHER.mkv"
    )

    assert _logical_identity(high) == _logical_identity(low)


def test_distinct_extra_titles_do_not_share_planner_logical_identity() -> None:
    drawing = _extra_record(
        "Fixture Series/Fixture.Series.S01EXTRA.How.To.Draw.Character.mkv"
    )
    music = _extra_record("Fixture Series/Fixture.Series.S01EXTRA.Music.Video.mkv")

    assert _logical_identity(drawing) != _logical_identity(music)


def test_generic_extra_folder_filename_can_supply_semantic_title() -> None:
    naming = derive_extra_display_identity(
        "Fixture Series/Extras/Fixture.Series.Cast.Reunion.1080p.WEB-DL.x265-GROUP.mkv",
        "extra",
        show_title="Fixture Series",
    )

    assert naming.display_title == "Cast Reunion"
    assert naming.identity_key == "cast reunion"


def test_unsafe_semantic_title_is_preserved_then_sanitized_losslessly() -> None:
    decision = _destination(
        "Fixture Series/Fixture.Series.S01EXTRA.Part:One?.1080p.WEB-DL.mkv"
    )

    assert decision.status is DestinationStatus.READY
    assert decision.relative_path is not None
    assert decision.relative_path.endswith("Part~003AOne~003F.mkv")
    assert "extra-display-title:Part:One?" in decision.reasons


def test_generic_extra_without_useful_identity_keeps_safe_fallback() -> None:
    decision = _destination("Fixture Series/Fixture.Series.S01EXTRA.1080p.WEB-DL.mkv")

    assert decision.status is DestinationStatus.READY
    assert decision.relative_path == "Fixture Series (2026)/extras/Extra.mkv"
    assert "extra-naming-source:kind-fallback" in decision.reasons
