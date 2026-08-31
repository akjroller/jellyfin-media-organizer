from __future__ import annotations

import unicodedata

import pytest

from jellyfin_show_organizer.destination import (
    DestinationPolicy,
    DestinationStatus,
    JellyfinProvider,
    JellyfinProviderIdentifier,
    build_episode_destination,
    build_extra_destination,
    find_destination_collisions,
    sanitize_component,
)
from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    ProviderEpisode,
    SourceEpisodeAssignment,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    ExtraDecision,
    MatchEvidence,
    NumberingMode,
)


def _show(title: str = "Example Series", year: int | None = 2024) -> CanonicalShow:
    return CanonicalShow(
        source_key=title,
        tvmaze_id=4242,
        title=title,
        year=year,
        numbering_mode=NumberingMode.AIRED,
    )


def _episode(
    episode_id: int,
    season: int,
    number: int | None,
    title: str,
) -> ProviderEpisode:
    return ProviderEpisode(
        tvmaze_episode_id=episode_id,
        season=season,
        number=number,
        title=title,
    )


def _assignment(
    source_key: str,
    *episodes: ProviderEpisode,
) -> SourceEpisodeAssignment:
    return SourceEpisodeAssignment(
        source_key=source_key,
        status=AssignmentStatus.MATCHED,
        episodes=episodes,
        evidence=MatchEvidence(method="test", confidence=1.0),
    )


def test_default_episode_layout_matches_jellyfin_series_and_season_policy() -> None:
    decision = build_episode_destination(
        _show(),
        _assignment("source.mkv", _episode(1001, 1, 1, "Pilot")),
        ".MKV",
    )

    assert decision.status is DestinationStatus.READY
    assert decision.relative_path == (
        "Example Series (2024)/Season 01/"
        "Example Series (2024) S01E01 - Pilot.mkv"
    )
    assert "canonical-tvmaze-id:4242" in decision.reasons
    assert "tvmaze-id-retained-as-audit-only-identity" in decision.reasons
    assert "tvmazeid" not in decision.relative_path.casefold()


def test_supported_jellyfin_provider_tag_is_added_to_series_folder_only() -> None:
    decision = build_episode_destination(
        _show(),
        _assignment("source.mkv", _episode(1001, 1, 1, "Pilot")),
        ".mkv",
        provider_ids=(
            JellyfinProviderIdentifier(JellyfinProvider.TVDB, "12345"),
        ),
    )

    assert decision.relative_path == (
        "Example Series (2024) [tvdbid-12345]/Season 01/"
        "Example Series (2024) S01E01 - Pilot.mkv"
    )
    assert "jellyfin-provider-tag:[tvdbid-12345]" in decision.reasons


def test_provider_tags_are_sorted_and_conflicts_fail_closed() -> None:
    providers = (
        JellyfinProviderIdentifier(JellyfinProvider.TVDB, "22"),
        JellyfinProviderIdentifier(JellyfinProvider.IMDB, "TT123"),
        JellyfinProviderIdentifier(JellyfinProvider.TMDB, "11"),
    )
    decision = build_episode_destination(
        _show(),
        _assignment("source.mkv", _episode(1001, 1, 1, "Pilot")),
        ".mkv",
        provider_ids=providers,
    )

    assert decision.relative_path is not None
    assert decision.relative_path.startswith(
        "Example Series (2024) [imdbid-tt123] [tmdbid-11] [tvdbid-22]/"
    )

    conflict = build_episode_destination(
        _show(),
        _assignment("source.mkv", _episode(1001, 1, 1, "Pilot")),
        ".mkv",
        provider_ids=(
            JellyfinProviderIdentifier(JellyfinProvider.TVDB, "22"),
            JellyfinProviderIdentifier(JellyfinProvider.TVDB, "23"),
        ),
    )
    assert conflict.status is DestinationStatus.UNRESOLVED
    assert conflict.relative_path is None
    assert conflict.reasons[0].startswith("invalid-destination-input:")


def test_specials_use_season_zero_without_rewriting_episode_identity() -> None:
    decision = build_episode_destination(
        _show(),
        _assignment("special.mkv", _episode(9001, 0, 1, "Preview Special")),
        ".mkv",
    )

    assert decision.relative_path == (
        "Example Series (2024)/Season 00/"
        "Example Series (2024) S00E01 - Preview Special.mkv"
    )


def test_contiguous_multi_episode_source_uses_jellyfin_range_syntax() -> None:
    decision = build_episode_destination(
        _show(),
        _assignment(
            "double.mkv",
            _episode(1001, 1, 1, "Part Alpha"),
            _episode(1002, 1, 2, "Part Beta"),
        ),
        ".mkv",
    )

    assert decision.relative_path == (
        "Example Series (2024)/Season 01/"
        "Example Series (2024) S01E01-E02 - Part Alpha + Part Beta.mkv"
    )


def test_noncontiguous_or_cross_season_multi_episode_sources_fail_closed() -> None:
    noncontiguous = build_episode_destination(
        _show(),
        _assignment(
            "gap.mkv",
            _episode(1001, 1, 1, "One"),
            _episode(1003, 1, 3, "Three"),
        ),
        ".mkv",
    )
    assert noncontiguous.status is DestinationStatus.UNRESOLVED
    assert (
        "multi-episode-source-is-not-one-contiguous-ascending-range"
        in noncontiguous.reasons
    )

    cross_season = build_episode_destination(
        _show(),
        _assignment(
            "cross.mkv",
            _episode(1002, 1, 2, "End"),
            _episode(2001, 2, 1, "Start"),
        ),
        ".mkv",
    )
    assert cross_season.status is DestinationStatus.UNRESOLVED
    assert "multi-episode-source-spans-seasons" in cross_season.reasons


def test_unmatched_episode_assignment_cannot_receive_destination() -> None:
    assignment = SourceEpisodeAssignment(
        source_key="unknown.mkv",
        status=AssignmentStatus.UNRESOLVED,
        episodes=(),
        evidence=MatchEvidence(method="test", confidence=0.0),
    )
    decision = build_episode_destination(_show(), assignment, ".mkv")

    assert decision.status is DestinationStatus.UNRESOLVED
    assert decision.relative_path is None
    assert decision.reasons == ("episode-assignment-not-matched:unresolved",)


@pytest.mark.parametrize(
    ("kind", "folder"),
    [
        ("trailer", "trailers"),
        ("featurette", "featurettes"),
        ("interview", "interviews"),
        ("behind-the-scenes", "behind the scenes"),
        ("deleted-scene", "deleted scenes"),
        ("clip", "clips"),
        ("extra", "extras"),
        ("creditless-opening", "extras"),
        ("creditless-ending", "extras"),
    ],
)
def test_extras_use_explicit_jellyfin_compatible_folders(kind: str, folder: str) -> None:
    decision = build_extra_destination(
        _show(),
        source_key=f"{kind}.mkv",
        extra=ExtraDecision(kind=kind, rule="synthetic-test"),
        source_extension=".mkv",
        display_title="Supplemental Video",
    )

    assert decision.status is DestinationStatus.READY
    assert decision.relative_path == (
        f"Example Series (2024)/{folder}/Supplemental Video.mkv"
    )


def test_unknown_extra_kind_uses_generic_folder_and_records_reason() -> None:
    decision = build_extra_destination(
        _show(),
        source_key="unknown.mkv",
        extra=ExtraDecision(kind="future-kind", rule="synthetic-test"),
        source_extension=".mkv",
    )

    assert decision.relative_path == "Example Series (2024)/extras/Extra.mkv"
    assert "unknown-extra-kind-mapped-to-generic-extras-folder" in decision.reasons


def test_sanitizer_encodes_forbidden_trailing_and_reserved_values() -> None:
    assert sanitize_component("A:B") != sanitize_component("A?B")
    assert sanitize_component("A:B") == "A~003AB"
    assert sanitize_component("A?B") == "A~003FB"
    assert sanitize_component("Name. ") == "Name~002E~0020"
    assert sanitize_component("CON") == "~R~CON"
    assert sanitize_component("con.txt") == "~R~con.txt"
    assert sanitize_component("literal~escape") == "literal~~escape"


def test_sanitizer_normalizes_unicode_to_nfc() -> None:
    decomposed = "Cafe\N{COMBINING ACUTE ACCENT}"
    sanitized = sanitize_component(decomposed)

    assert sanitized == unicodedata.normalize("NFC", decomposed)
    assert sanitized == "Café"


def test_case_insensitive_destination_collisions_are_reported() -> None:
    upper = build_episode_destination(
        _show("Case Series"),
        _assignment("upper.mkv", _episode(1001, 1, 1, "Pilot")),
        ".mkv",
    )
    lower = build_episode_destination(
        _show("case series"),
        _assignment("lower.mkv", _episode(1001, 1, 1, "pilot")),
        ".mkv",
    )

    collisions = find_destination_collisions((upper, lower))

    assert len(collisions) == 1
    assert collisions[0].source_keys == ("lower.mkv", "upper.mkv")
    assert len(collisions[0].relative_paths) == 2


def test_loss_preserving_sanitization_avoids_false_forbidden_character_collision() -> None:
    colon = build_episode_destination(
        _show("Series: Name"),
        _assignment("colon.mkv", _episode(1001, 1, 1, "Pilot")),
        ".mkv",
    )
    question = build_episode_destination(
        _show("Series? Name"),
        _assignment("question.mkv", _episode(1001, 1, 1, "Pilot")),
        ".mkv",
    )

    assert colon.relative_path != question.relative_path
    assert find_destination_collisions((colon, question)) == ()


def test_path_limit_shortening_is_deterministic_and_bounded() -> None:
    show = _show("Extremely Long Synthetic Series Name " * 5)
    assignment = _assignment(
        "long.mkv",
        _episode(1001, 1, 1, "Extremely Long Synthetic Episode Title " * 5),
    )
    policy = DestinationPolicy(max_path_length=150, max_component_length=120)

    first = build_episode_destination(show, assignment, ".mkv", policy=policy)
    second = build_episode_destination(show, assignment, ".mkv", policy=policy)

    assert first == second
    assert first.status is DestinationStatus.READY
    assert first.relative_path is not None
    assert len(first.relative_path) <= 150
    assert any("shortened-for-path-limit" in reason for reason in first.reasons)
    assert "~h" in first.relative_path


def test_configured_path_limit_fails_closed_when_minimum_structure_cannot_fit() -> None:
    show = _show("Long Synthetic Series Name " * 8)
    assignment = _assignment(
        "long.mkv",
        _episode(1001, 123456, 123456, "Long Synthetic Episode Title " * 8),
    )
    policy = DestinationPolicy(max_path_length=80, max_component_length=180)

    decision = build_episode_destination(show, assignment, ".mkv", policy=policy)

    assert decision.status is DestinationStatus.UNRESOLVED
    assert decision.relative_path is None
    assert "path-length-limit-cannot-be-satisfied" in decision.reasons


def test_invalid_extension_fails_closed() -> None:
    decision = build_episode_destination(
        _show(),
        _assignment("source.file", _episode(1001, 1, 1, "Pilot")),
        ".mkv/evil",
    )

    assert decision.status is DestinationStatus.UNRESOLVED
    assert decision.relative_path is None
    assert decision.reasons[0].startswith("invalid-destination-input:")


def test_destination_builder_does_not_need_source_filename_parsing() -> None:
    decision = build_episode_destination(
        _show(),
        _assignment("opaque-source-key", _episode(1001, 2, 7, "Resolved Title")),
        ".avi",
    )

    assert decision.relative_path == (
        "Example Series (2024)/Season 02/"
        "Example Series (2024) S02E07 - Resolved Title.avi"
    )
    assert "opaque-source-key" not in decision.relative_path
