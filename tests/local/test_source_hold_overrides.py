from __future__ import annotations

from pathlib import Path

import pytest

from jellyfin_show_organizer.apply_contract import _video_member
from jellyfin_show_organizer.models import (
    MatchEvidence,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.planner import _video_preflight_status
from jellyfin_show_organizer.preflight import PreflightStatus
from jellyfin_show_organizer.schema import PLAN_SCHEMA_VERSION, plan_to_manifest

pytestmark = pytest.mark.local


def _source(path: str = "Example/Unsupported.mkv") -> SourceFile:
    return SourceFile(
        relative_path=path,
        extension=".mkv",
        fingerprint=SourceFingerprint(size=123, mtime_ns=456, sha256=None),
    )


def _held_record(path: str = "Example/Unsupported.mkv") -> PlanRecord:
    return PlanRecord(
        source=_source(path),
        status=TerminalStatus.HELD,
        parse=ParseResult(series_hint="Example"),
        evidence=MatchEvidence(
            method="source-hold-override",
            confidence=1.0,
            reasons=("provider has no safe episode coordinate",),
        ),
        operation_group_id="op-example",
        reason="provider has no safe episode coordinate",
    )


def test_schema_v4_loads_exact_source_hold_and_hashes_it(tmp_path: Path) -> None:
    path = tmp_path / "overrides.toml"
    path.write_text(
        """schema_version = 4

[[source_holds]]
source = "Example/Unsupported.mkv"
reasons = ["provider has no safe episode coordinate"]
""",
        encoding="utf-8",
    )

    catalog = load_overrides(path)
    hold = catalog.source_hold_for("example/unsupported.mkv")

    assert hold is not None
    assert hold.source == "Example/Unsupported.mkv"
    assert hold.reasons == ("provider has no safe episode coordinate",)
    assert len(catalog.snapshot_id) == 64


def test_source_hold_cannot_overlap_episode_or_duplicate_decision(tmp_path: Path) -> None:
    path = tmp_path / "overrides.toml"
    path.write_text(
        """schema_version = 4

[[duplicate_preferences]]
source = "Example/Unsupported.mkv"
rank = 10

[[source_holds]]
source = "example/unsupported.mkv"
reasons = ["leave this source in place"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot overlap"):
        load_overrides(path)


def test_held_record_is_first_class_plan_status_and_non_moving() -> None:
    record = _held_record()
    plan = OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=4,
        records=(record,),
    )

    manifest = plan_to_manifest(plan)

    assert PLAN_SCHEMA_VERSION == 2
    assert manifest["records"][0]["status"] == "held"
    assert manifest["records"][0]["destination"] is None
    assert _video_preflight_status(record) is PreflightStatus.NON_MOVING
    assert _video_member(manifest["records"][0], 0) is None


def test_held_record_rejects_destinations_and_provider_episodes() -> None:
    record = _held_record()

    with pytest.raises(ValueError, match="non-moving"):
        PlanRecord(
            source=record.source,
            status=TerminalStatus.HELD,
            parse=record.parse,
            evidence=record.evidence,
            destination="Example/Season 01/Example - S01E01.mkv",
            reason=record.reason,
        )
