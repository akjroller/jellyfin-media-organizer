import socket

import pytest

from jellyfin_show_organizer.extra_classifier import (
    ExtraDisposition,
    ExtraKind,
    classify_extra,
)

pytestmark = pytest.mark.local


@pytest.mark.parametrize(
    ("relative_path", "expected_kind"),
    [
        (
            "synthetic/Starforge Academy/[SYNTH] Starforge Academy - NCOP [1080p].mkv",
            ExtraKind.CREDITLESS_OPENING,
        ),
        (
            "synthetic/Starforge Academy/Starforge.Academy.Creditless.Ending.mp4",
            ExtraKind.CREDITLESS_ENDING,
        ),
        (
            "synthetic/Mirror City/Mirror.City.Trailer.01.1080p-SYNTH.mkv",
            ExtraKind.TRAILER,
        ),
        (
            "synthetic/Mirror City/Mirror.City.Featurette.03.1080p-SYNTH.mkv",
            ExtraKind.FEATURETTE,
        ),
        (
            "synthetic/River Patrol/River.Patrol.Interview.With.The.Cast.mkv",
            ExtraKind.INTERVIEW,
        ),
        (
            "synthetic/Signal Ridge/Signal.Ridge.Behind.The.Scenes.02.mp4",
            ExtraKind.BEHIND_THE_SCENES,
        ),
        (
            "synthetic/Old Harbor/Old.Harbor.Deleted.Scene.Clip.04.mkv",
            ExtraKind.DELETED_SCENE,
        ),
        (
            "synthetic/Bubble Borough/Bubble.Borough.Clip.07.mkv",
            ExtraKind.CLIP,
        ),
        (
            "synthetic/Northstar Files/Extras/Northstar.Files.Cast.Reunion.mkv",
            ExtraKind.EXTRA,
        ),
    ],
)
def test_explicit_extra_families_normalize_to_documented_kinds(
    relative_path: str,
    expected_kind: ExtraKind,
):
    result = classify_extra(relative_path)

    assert result.disposition is ExtraDisposition.EXTRA
    assert result.decision is not None
    assert result.decision.kind == expected_kind.value
    assert result.decision.rule in result.reasons


def test_strong_extra_marker_beats_weak_numeric_noise():
    result = classify_extra(
        "synthetic/Mirror City/Mirror.City.Trailer.01.1080p-SYNTH.mkv"
    )

    assert result.disposition is ExtraDisposition.EXTRA
    assert result.decision is not None
    assert result.decision.kind == ExtraKind.TRAILER.value
    assert result.parse.season is None
    assert result.parse.absolute_episode is None


def test_strong_episode_and_extra_evidence_fails_closed():
    result = classify_extra(
        "synthetic/River Patrol/River.Patrol.S01E03.Cast.Interview.mkv"
    )

    assert result.disposition is ExtraDisposition.SUSPICIOUS
    assert result.decision is None
    assert result.parse.season == 1
    assert result.parse.episodes == (3,)
    assert "conflicts" in result.reasons[0]


def test_conflicting_extra_markers_fail_closed():
    result = classify_extra(
        "synthetic/Signal Ridge/Signal.Ridge.Trailer.Interview.1080p.mkv"
    )

    assert result.disposition is ExtraDisposition.SUSPICIOUS
    assert result.decision is None
    assert result.reasons[0] == "conflicting explicit extra markers"


def test_ambiguous_extra_word_without_episode_evidence_is_unresolved():
    result = classify_extra(
        "synthetic/Old Harbor/Old.Harbor.Bonus.Production.Reel.mkv"
    )

    assert result.disposition is ExtraDisposition.UNRESOLVED
    assert result.decision is None
    assert "requires review" in result.reasons[0]


@pytest.mark.parametrize("word", ["Special", "Bonus", "Clip", "Extra"])
def test_weak_title_words_do_not_downgrade_strong_episode_evidence(word: str):
    result = classify_extra(
        f"synthetic/Old Harbor/Old.Harbor.S01E03.{word}.Delivery.mkv"
    )

    assert result.disposition is ExtraDisposition.EPISODE_CANDIDATE
    assert result.decision is None
    assert result.parse.season == 1
    assert result.parse.episodes == (3,)


@pytest.mark.parametrize(
    "relative_path",
    [
        "synthetic/Mirror City/Mirror.City.S01EXTRA.mkv",
        "synthetic/Mirror City/Mirror.City.S01EXTRAS.mkv",
        "synthetic/Mirror City/Mirror.City.s01extra01.mkv",
        "synthetic/Mirror City/Mirror.City.S01.Extra.02.mkv",
    ],
)
def test_structural_season_extra_markers_are_explicit_extras(relative_path: str):
    result = classify_extra(relative_path)

    assert result.disposition is ExtraDisposition.EXTRA
    assert result.decision is not None
    assert result.decision.kind == ExtraKind.EXTRA.value
    assert result.decision.rule == "structural season-extra marker"


def test_embedded_extra_parent_directory_is_structural_evidence():
    result = classify_extra(
        "synthetic/Mirror City/Mirror.City.S01.EXTRAS.1080p/Cast.Reunion.mkv"
    )

    assert result.disposition is ExtraDisposition.EXTRA
    assert result.decision is not None
    assert result.decision.kind == ExtraKind.EXTRA.value
    assert result.decision.rule == "embedded extra folder marker"


@pytest.mark.parametrize("marker", ["OVA", "OAD"])
def test_ova_and_oad_route_to_special_numbering_instead_of_generic_extra(
    marker: str,
):
    result = classify_extra(
        f"synthetic/Starforge Academy/Starforge.Academy.{marker}.01.mkv"
    )

    assert result.disposition is ExtraDisposition.EPISODE_CANDIDATE
    assert result.decision is None
    assert result.reasons == (f"special-numbering marker: {marker.casefold()}",)


def test_normal_episode_remains_episode_candidate():
    result = classify_extra(
        "synthetic/Bubble Borough/Bubble.Borough.S01E01a.Red.Kite.mkv"
    )

    assert result.disposition is ExtraDisposition.EPISODE_CANDIDATE
    assert result.decision is None
    assert result.parse.season == 1
    assert result.parse.episodes == (1,)
    assert result.parse.segment_hint == "a"


def test_immediate_extra_folder_can_supply_specific_kind():
    result = classify_extra(
        "synthetic/Northstar Files/Featurettes/Building.The.Signal.Room.mkv"
    )

    assert result.disposition is ExtraDisposition.EXTRA
    assert result.decision is not None
    assert result.decision.kind == ExtraKind.FEATURETTE.value
    assert result.decision.rule == "extra folder: featurette"


def test_classifier_is_deterministic_and_network_independent(monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("extra classification must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    path = "synthetic/Mirror City/Mirror.City.Trailer.01.mkv"

    assert classify_extra(path) == classify_extra(path)
