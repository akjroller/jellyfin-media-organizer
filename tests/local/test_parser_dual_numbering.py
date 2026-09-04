from __future__ import annotations

import pytest

from jellyfin_show_organizer.filename_parser import parse_video_path
from jellyfin_show_organizer.models import (
    OrganizerPlan,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.schema import plan_to_manifest

pytestmark = pytest.mark.local


@pytest.mark.parametrize(
    "relative_path",
    (
        "Example Series S03E24 (107).mkv",
        "Example Series S03E24 [107].mkv",
        "Example Series S03E24 - [107].mkv",
        "Example Series S03E24._(107).mkv",
    ),
)
def test_dual_numbering_preserves_aired_and_absolute_evidence(
    relative_path: str,
) -> None:
    result = parse_video_path(relative_path)

    assert result.series_hint == "Example Series"
    assert result.season == 3
    assert result.episodes == (24,)
    assert result.absolute_episode == 107
    assert result.title_hint is None


def test_dual_numbering_skips_secondary_token_when_extracting_title() -> None:
    result = parse_video_path("Example Series S03E24 - (107) - Final Chapter.mkv")

    assert result.season == 3
    assert result.episodes == (24,)
    assert result.absolute_episode == 107
    assert result.title_hint == "Final Chapter"


@pytest.mark.parametrize(
    "relative_path",
    (
        "Example Series S03E24 (2024).mkv",
        "Example Series S03E24 [1080p].mkv",
        "Example Series S03E24 [A1B2C3D4].mkv",
        "Example Series S03E24 (107) [108].mkv",
        "Example Series S03E24 (107-108).mkv",
    ),
)
def test_misleading_or_ambiguous_secondary_tokens_are_not_absolute_evidence(
    relative_path: str,
) -> None:
    result = parse_video_path(relative_path)

    assert result.season == 3
    assert result.episodes == (24,)
    assert result.absolute_episode is None


def test_existing_single_aired_parse_result_is_unchanged() -> None:
    result = parse_video_path("Example Series S03E24 - Final Chapter.mkv")

    assert result.series_hint == "Example Series"
    assert result.season == 3
    assert result.episodes == (24,)
    assert result.absolute_episode is None
    assert result.title_hint == "Final Chapter"


def test_dual_evidence_serializes_in_plan_schema_v1_deterministically() -> None:
    parse = parse_video_path("Example Series S03E24 (107).mkv")
    source = SourceFile(
        relative_path="Example Series S03E24 (107).mkv",
        extension=".mkv",
        fingerprint=SourceFingerprint(size=123, mtime_ns=456),
    )
    plan = OrganizerPlan(
        schema_version=2,
        overrides_version=1,
        records=(
            PlanRecord(
                source=source,
                status=TerminalStatus.UNRESOLVED,
                parse=parse,
                operation_group_id="op-synthetic",
                reason="synthetic dual-numbering evidence",
            ),
        ),
    )

    first = plan_to_manifest(plan)
    second = plan_to_manifest(plan)
    parse_payload = first["records"][0]["parse"]

    assert first == second
    assert parse_payload["season"] == 3
    assert parse_payload["episodes"] == [24]
    assert parse_payload["absolute_episode"] == 107
