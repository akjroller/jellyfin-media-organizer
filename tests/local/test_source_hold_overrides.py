from __future__ import annotations

from pathlib import Path

import pytest

from jellyfin_show_organizer.apply_contract import _video_member
from jellyfin_show_organizer.models import (
    CompanionStatus,
    MatchEvidence,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.planner import (
    PlanningConfigurationError,
    _plan_companions,
    _preflight_records,
    _validate_source_hold_sources,
    _video_preflight_status,
)
from jellyfin_show_organizer.preflight import PreflightStatus
from jellyfin_show_organizer.schema import PLAN_SCHEMA_VERSION, plan_to_manifest
from jellyfin_show_organizer.sidecars import (
    AdjacentDisposition,
    AdjacentFile,
    CompanionGroup,
    CompanionKind,
    SidecarDiscovery,
)

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

    original_snapshot = catalog.snapshot_id
    path.write_text(
        """schema_version = 4

[[source_holds]]
source = "Example/Unsupported.mkv"
reasons = ["reviewed and intentionally left in place"]
""",
        encoding="utf-8",
    )
    assert load_overrides(path).snapshot_id != original_snapshot


def test_source_hold_cannot_overlap_episode_or_duplicate_decision(
    tmp_path: Path,
) -> None:
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


def test_source_hold_rejects_unknown_included_video(tmp_path: Path) -> None:
    path = tmp_path / "overrides.toml"
    path.write_text(
        """schema_version = 4

[[source_holds]]
source = "Example/Missing.mkv"
reasons = ["leave this source in place"]
""",
        encoding="utf-8",
    )

    catalog = load_overrides(path)
    with pytest.raises(PlanningConfigurationError, match="unknown source"):
        _validate_source_hold_sources((_source(),), catalog)


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


def test_held_video_companion_is_ignored_and_non_moving() -> None:
    record = _held_record()
    subtitle = AdjacentFile(
        relative_path="Example/Unsupported.en.srt",
        extension=".srt",
        fingerprint=SourceFingerprint(size=42, mtime_ns=99, sha256=None),
        disposition=AdjacentDisposition.ASSOCIATED,
        reason="subtitle stem matches video",
    )
    discovery = SidecarDiscovery(
        companions=(
            CompanionGroup(
                source_video=record.source.relative_path,
                kind=CompanionKind.SUBTITLE,
                suffix=".en",
                files=(subtitle,),
            ),
        ),
        unresolved=(),
        ignored=(),
    )

    companions = _plan_companions(discovery, (record,))

    assert len(companions) == 1
    companion = companions[0]
    assert companion.status is CompanionStatus.IGNORED
    assert companion.destination is None
    assert companion.source_video == record.source.relative_path

    plan = OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=4,
        records=(record,),
        companions=companions,
    )
    preflight = _preflight_records(plan)
    companion_preflight = next(
        item for item in preflight if item.record_id.startswith("companion:")
    )
    assert companion_preflight.status is PreflightStatus.NON_MOVING
