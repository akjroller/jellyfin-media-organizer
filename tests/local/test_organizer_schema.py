import json
from copy import deepcopy
from pathlib import Path

import pytest

from jellyfin_show_organizer.models import (
    CanonicalShow,
    DuplicateCollisionClass,
    DuplicateDecision,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.schema import (
    LEGACY_PLAN_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    ManifestValidationError,
    load_plan_schema,
    plan_to_manifest,
    stable_plan_hash,
    validate_manifest,
)

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]


def _plan(*, size: int = 1024) -> OrganizerPlan:
    return OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=1,
        records=(
            PlanRecord(
                source=SourceFile(
                    relative_path="Example Series/Example Series S01E01.mkv",
                    extension=".mkv",
                    fingerprint=SourceFingerprint(size=size, mtime_ns=123456789),
                ),
                status=TerminalStatus.MATCHED,
                parse=ParseResult(
                    series_hint="Example Series",
                    season=1,
                    episodes=(1,),
                ),
                show=CanonicalShow(
                    source_key="example-series",
                    tvmaze_id=12345,
                    title="Example Series",
                    year=2000,
                    numbering_mode=NumberingMode.AIRED,
                ),
                evidence=MatchEvidence(
                    method="synthetic-test-match",
                    confidence=1.0,
                    reasons=("synthetic fixture",),
                ),
                destination="Example Series/Season 01/Example Series S01E01.mkv",
            ),
        ),
    )


def _duplicate_plan() -> OrganizerPlan:
    source = "Fabricated Series/release-a.mkv"
    other = "Fabricated Series/release-b.mkv"
    return OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=4,
        records=(
            PlanRecord(
                source=SourceFile(
                    relative_path=source,
                    extension=".mkv",
                    fingerprint=SourceFingerprint(size=100, mtime_ns=200),
                ),
                status=TerminalStatus.DUPLICATE,
                duplicate=DuplicateDecision(
                    destination_key=(
                        "Fabricated Series/Season 01/Fabricated Series S01E01.mkv"
                    ),
                    candidates=(source, other),
                    winner=source,
                    losers=(other,),
                    confidence=1.0,
                    evidence=("fabricated exact duplicate",),
                    collision_class=DuplicateCollisionClass.SAME_LOGICAL_IDENTITY,
                ),
            ),
        ),
    )


def test_checked_in_current_schema_is_versioned():
    schema = load_plan_schema()

    assert schema["properties"]["schema_version"]["const"] == PLAN_SCHEMA_VERSION
    assert schema["additionalProperties"] is False
    assert "planRecord" in schema["$defs"]


def test_frozen_schema_v2_resource_remains_immutable_for_duplicate_shape():
    schema = load_plan_schema(LEGACY_PLAN_SCHEMA_VERSION)
    duplicate = schema["$defs"]["duplicate"]

    assert schema["properties"]["schema_version"]["const"] == 2
    assert "collision_class" not in duplicate["required"]
    assert "collision_class" not in duplicate["properties"]


def test_frozen_schema_v2_duplicate_manifest_still_validates():
    manifest = json.loads(
        (ROOT / "tests/fixtures/plan-schema-v2-duplicate.json").read_text(
            encoding="utf-8"
        )
    )

    validate_manifest(manifest)
    assert manifest["schema_version"] == LEGACY_PLAN_SCHEMA_VERSION
    assert "collision_class" not in manifest["records"][0]["duplicate"]


def test_plan_hash_is_stable_for_equivalent_plans():
    first = _plan()
    second = _plan()

    assert stable_plan_hash(first) == stable_plan_hash(second)
    assert len(stable_plan_hash(first)) == 64


def test_plan_hash_changes_when_source_fingerprint_changes():
    assert stable_plan_hash(_plan(size=1024)) != stable_plan_hash(_plan(size=2048))


def test_serialized_plan_v3_validates():
    manifest = plan_to_manifest(_plan())

    validate_manifest(manifest)
    assert manifest["schema_version"] == PLAN_SCHEMA_VERSION
    assert manifest["records"][0]["status"] == "matched"


def test_schema_v3_duplicate_round_trip_requires_collision_class():
    manifest = plan_to_manifest(_duplicate_plan())
    duplicate = manifest["records"][0]["duplicate"]

    assert duplicate["collision_class"] == "same-logical-identity"
    validate_manifest(manifest)

    del duplicate["collision_class"]
    with pytest.raises(ManifestValidationError, match="unexpected fields"):
        validate_manifest(manifest)


def test_manifest_rejects_unknown_schema_version():
    manifest = deepcopy(plan_to_manifest(_plan()))
    manifest["schema_version"] = 999

    with pytest.raises(ManifestValidationError, match="unsupported schema_version"):
        validate_manifest(manifest)


def test_manifest_rejects_matched_record_without_destination():
    manifest = deepcopy(plan_to_manifest(_plan()))
    manifest["records"][0]["destination"] = None

    with pytest.raises(ManifestValidationError, match="require destination"):
        validate_manifest(manifest)
