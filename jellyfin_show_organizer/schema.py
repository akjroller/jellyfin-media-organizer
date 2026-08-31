from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict
from importlib.resources import files
from typing import Any, cast

from .models import OrganizerPlan, PlanRecord

PLAN_SCHEMA_VERSION = 1
PLAN_SCHEMA_RESOURCE = "data/plan-schema-v1.json"


class ManifestValidationError(ValueError):
    """Raised when serialized plan data does not satisfy the current schema."""


def load_plan_schema() -> dict[str, Any]:
    resource = files("jellyfin_show_organizer").joinpath(PLAN_SCHEMA_RESOURCE)
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


def _path_sort_key(relative_path: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", relative_path).casefold()
    return normalized, relative_path


def canonical_records(plan: OrganizerPlan) -> tuple[PlanRecord, ...]:
    """Return plan records in a platform-independent deterministic order."""

    return tuple(
        sorted(plan.records, key=lambda record: _path_sort_key(record.source.relative_path))
    )


def plan_to_manifest(plan: OrganizerPlan) -> dict[str, Any]:
    payload = cast(
        dict[str, Any],
        json.loads(
            json.dumps(
                {
                    "schema_version": plan.schema_version,
                    "overrides_version": plan.overrides_version,
                    "records": [asdict(record) for record in canonical_records(plan)],
                },
                ensure_ascii=False,
            )
        ),
    )
    validate_manifest(payload)
    return payload


def canonical_manifest_bytes(plan: OrganizerPlan) -> bytes:
    return json.dumps(
        plan_to_manifest(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def stable_plan_hash(plan: OrganizerPlan) -> str:
    return hashlib.sha256(canonical_manifest_bytes(plan)).hexdigest()


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ManifestValidationError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _require_string(value: object, field: str, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"{field} must be a non-empty string")


def _require_number(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ManifestValidationError(f"{field} must be a number")


def _validate_fingerprint(value: object, field: str) -> None:
    fingerprint = _require_mapping(value, field)
    required = {"size", "mtime_ns", "sha256"}
    if set(fingerprint) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    for key in ("size", "mtime_ns"):
        current = fingerprint[key]
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise ManifestValidationError(f"{field}.{key} must be non-negative")
    sha256 = fingerprint["sha256"]
    if sha256 is not None:
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise ManifestValidationError(f"{field}.sha256 must be 64 characters")
        try:
            int(sha256, 16)
        except ValueError as exc:
            raise ManifestValidationError(
                f"{field}.sha256 must contain hexadecimal characters"
            ) from exc


def _validate_source(value: object, field: str) -> None:
    source = _require_mapping(value, field)
    required = {"relative_path", "extension", "fingerprint"}
    if set(source) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _require_string(source["relative_path"], f"{field}.relative_path")
    _require_string(source["extension"], f"{field}.extension")
    extension = cast(str, source["extension"])
    if not extension.startswith("."):
        raise ManifestValidationError(f"{field}.extension must start with '.'")
    _validate_fingerprint(source["fingerprint"], f"{field}.fingerprint")


def _validate_record(value: object, index: int) -> None:
    field = f"records[{index}]"
    record = _require_mapping(value, field)
    required = {
        "source",
        "status",
        "parse",
        "show",
        "evidence",
        "destination",
        "extra",
        "duplicate",
    }
    if set(record) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")

    _validate_source(record["source"], f"{field}.source")
    _require_string(record["status"], f"{field}.status")
    status = cast(str, record["status"])
    allowed_statuses = {"matched", "extra", "duplicate", "suspicious", "unresolved"}
    if status not in allowed_statuses:
        raise ManifestValidationError(f"{field}.status is not supported")
    _require_string(record["destination"], f"{field}.destination", allow_none=True)

    if status == "matched":
        if (
            record["parse"] is None
            or record["show"] is None
            or record["evidence"] is None
        ):
            raise ManifestValidationError(
                f"{field} matched records require parse, show, and evidence"
            )
        if record["destination"] is None:
            raise ManifestValidationError(
                f"{field} matched records require destination"
            )
    if status == "extra" and record["extra"] is None:
        raise ManifestValidationError(f"{field} extra records require extra")
    if status == "duplicate" and record["duplicate"] is None:
        raise ManifestValidationError(f"{field} duplicate records require duplicate")

    if record["evidence"] is not None:
        evidence = _require_mapping(record["evidence"], f"{field}.evidence")
        if "confidence" not in evidence:
            raise ManifestValidationError(f"{field}.evidence.confidence is required")
        _require_number(evidence["confidence"], f"{field}.evidence.confidence")
        confidence = cast(float, evidence["confidence"])
        if not 0.0 <= confidence <= 1.0:
            raise ManifestValidationError(
                f"{field}.evidence.confidence must be between 0 and 1"
            )


def validate_manifest(value: object) -> None:
    manifest = _require_mapping(value, "manifest")
    required = {"schema_version", "overrides_version", "records"}
    if set(manifest) != required:
        raise ManifestValidationError("manifest has unexpected fields")

    if manifest["schema_version"] != PLAN_SCHEMA_VERSION:
        raise ManifestValidationError(
            f"unsupported schema_version: {manifest['schema_version']!r}"
        )
    overrides_version = manifest["overrides_version"]
    if (
        isinstance(overrides_version, bool)
        or not isinstance(overrides_version, int)
        or overrides_version <= 0
    ):
        raise ManifestValidationError("overrides_version must be a positive integer")

    records = manifest["records"]
    if not isinstance(records, list | tuple):
        raise ManifestValidationError("records must be an array")
    for index, record in enumerate(records):
        _validate_record(record, index)
