from __future__ import annotations

from pathlib import Path

import pytest

from jellyfin_show_organizer.destination import (
    DestinationPolicy,
    DestinationStatus,
    build_episode_destination,
)
from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    ProviderEpisode,
    SourceEpisodeAssignment,
)
from jellyfin_show_organizer.inventory import authorize_shows_root
from jellyfin_show_organizer.models import (
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    SourceFingerprint,
)
from jellyfin_show_organizer.preflight import (
    PreflightRecord,
    PreflightStatus,
    authorize_destination_root,
    preflight_plan,
)

pytestmark = pytest.mark.local


def _show(title: str = "Synthetic Boundary Series") -> CanonicalShow:
    return CanonicalShow(
        source_key=title,
        tvmaze_id=4242,
        title=title,
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )


def _assignment(title: str) -> SourceEpisodeAssignment:
    return SourceEpisodeAssignment(
        source_key="input.mkv",
        status=AssignmentStatus.MATCHED,
        episodes=(
            ProviderEpisode(
                tvmaze_episode_id=1001,
                season=1,
                number=1,
                title=title,
            ),
        ),
        evidence=MatchEvidence(method="synthetic-test", confidence=1.0),
    )


@pytest.mark.parametrize("extension", (".mkv", ".mp4", ".abcdefghijkl"))
def test_filename_component_limit_reserves_normalized_extension(extension: str) -> None:
    policy = DestinationPolicy(max_path_length=240, max_component_length=64)
    assignment = _assignment("Very Long Synthetic Episode Title " * 8)

    first = build_episode_destination(_show(), assignment, extension, policy=policy)
    second = build_episode_destination(_show(), assignment, extension, policy=policy)

    assert first == second
    assert first.status is DestinationStatus.READY
    assert first.relative_path is not None
    filename = first.relative_path.rsplit("/", 1)[-1]
    assert len(filename) <= policy.max_component_length
    assert filename.endswith(extension)
    assert "~h" in filename


def test_component_and_whole_path_shortening_compose_deterministically() -> None:
    policy = DestinationPolicy(max_path_length=120, max_component_length=64)
    decision = build_episode_destination(
        _show("Extremely Long Synthetic Series Name " * 5),
        _assignment("Extremely Long Synthetic Episode Title " * 7),
        ".mkv",
        policy=policy,
    )

    assert decision.status is DestinationStatus.READY
    assert decision.relative_path is not None
    assert len(decision.relative_path) <= policy.max_path_length
    assert all(
        len(component) <= policy.max_component_length
        for component in decision.relative_path.split("/")
    )
    assert "filename-component-sanitized" in decision.reasons
    assert "filename-shortened-for-path-limit" in decision.reasons


def test_extension_aware_ready_destination_passes_preflight_same_limits(
    tmp_path: Path,
) -> None:
    policy = DestinationPolicy(max_path_length=240, max_component_length=64)
    source_root_path = tmp_path / "source"
    destination_root_path = tmp_path / "destination"
    source_root_path.mkdir()
    destination_root_path.mkdir()
    source = source_root_path / "input.mkv"
    source.write_bytes(b"synthetic-media")
    stat = source.stat()

    decision = build_episode_destination(
        _show(),
        _assignment("Very Long Synthetic Episode Title " * 8),
        ".mkv",
        policy=policy,
    )
    assert decision.status is DestinationStatus.READY
    assert decision.relative_path is not None

    result = preflight_plan(
        "a" * 64,
        (
            PreflightRecord(
                record_id="video:input",
                source_relative_path="input.mkv",
                status=PreflightStatus.MATCHED,
                operation_group_id="group-1",
                provider_identity="tvmaze:4242",
                numbering_identity="aired:S01E01",
                destination_relative_path=decision.relative_path,
                source_fingerprint=SourceFingerprint(
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                ),
            ),
        ),
        source_root=authorize_shows_root(source_root_path),
        destination_root=authorize_destination_root(destination_root_path),
        max_path_length=policy.max_path_length,
        max_component_length=policy.max_component_length,
    )

    assert result.ready
    assert result.findings == ()
