from copy import deepcopy

import pytest

from jellyfin_show_organizer.models import (
    CanonicalShow,
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
    ManifestValidationError,
    load_plan_schema,
    plan_to_manifest,
    stable_plan_hash,
    validate_manifest,
)

pytestmark = pytest.mark.local


def _plan(*, size: int = 1024) -> OrganizerPlan:
    return OrganizerPlan(
        schema_version=1,
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


def test_checked_in_schema_is_versioned():
    schema = load_plan_schema()

    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["additionalProperties"] is False
    assert "planRecord" in schema["$defs"]


def test_plan_hash_is_stable_for_equivalent_plans():
    first = _plan()
    second = _plan()

    assert stable_plan_hash(first) == stable_plan_hash(second)
    assert len(stable_plan_hash(first)) == 64


def test_plan_hash_changes_when_source_fingerprint_changes():
    assert stable_plan_hash(_plan(size=1024)) != stable_plan_hash(_plan(size=2048))


def test_serialized_plan_validates():
    manifest = plan_to_manifest(_plan())

    validate_manifest(manifest)
    assert manifest["schema_version"] == 1
    assert manifest["records"][0]["status"] == "matched"


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
