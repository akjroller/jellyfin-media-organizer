from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from typing import Any, cast

from . import _schema_v3_impl as _impl

PLAN_SCHEMA_VERSION = 3
LEGACY_PLAN_SCHEMA_VERSION = 2
PLAN_SCHEMA_RESOURCE = "data/plan-schema-v3.json"
_PLAN_SCHEMA_RESOURCES = {
    LEGACY_PLAN_SCHEMA_VERSION: "data/plan-schema-v2.json",
    PLAN_SCHEMA_VERSION: PLAN_SCHEMA_RESOURCE,
}

ManifestValidationError = _impl.ManifestValidationError
canonical_records = _impl.canonical_records
canonical_companions = _impl.canonical_companions
plan_to_manifest = _impl.plan_to_manifest
canonical_manifest_bytes = _impl.canonical_manifest_bytes
stable_plan_hash = _impl.stable_plan_hash


def load_plan_schema(version: int = PLAN_SCHEMA_VERSION) -> dict[str, Any]:
    """Load one immutable packaged plan schema by explicit version."""

    resource_name = _PLAN_SCHEMA_RESOURCES.get(version)
    if resource_name is None:
        raise ManifestValidationError(f"unsupported schema_version: {version!r}")
    resource = files("jellyfin_show_organizer").joinpath(resource_name)
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    return _impl._require_mapping(value, field)


def _require_string(value: object, field: str, *, allow_none: bool = False) -> None:
    _impl._require_string(value, field, allow_none=allow_none)


def _require_number(value: object, field: str) -> None:
    _impl._require_number(value, field)


def _validate_duplicate_v2(value: object, field: str) -> None:
    """Validate the frozen v2 duplicate shape without deriving collision class."""

    duplicate = _require_mapping(value, field)
    required = {
        "destination_key",
        "candidates",
        "winner",
        "losers",
        "confidence",
        "evidence",
    }
    if set(duplicate) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _require_string(duplicate["destination_key"], f"{field}.destination_key")

    candidates = duplicate["candidates"]
    if not isinstance(candidates, list | tuple) or len(candidates) < 2:
        raise ManifestValidationError(f"{field}.candidates requires at least two items")
    if not all(isinstance(candidate, str) for candidate in candidates):
        raise ManifestValidationError(f"{field}.candidates must contain strings")

    winner = duplicate["winner"]
    if winner is not None and not isinstance(winner, str):
        raise ManifestValidationError(f"{field}.winner must be a string or null")

    losers = duplicate["losers"]
    if not isinstance(losers, list | tuple) or not all(
        isinstance(loser, str) for loser in losers
    ):
        raise ManifestValidationError(f"{field}.losers must contain strings")

    _require_number(duplicate["confidence"], f"{field}.confidence")
    confidence = cast(float, duplicate["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ManifestValidationError(f"{field}.confidence must be between 0 and 1")

    evidence = duplicate["evidence"]
    if not isinstance(evidence, list | tuple) or not all(
        isinstance(reason, str) for reason in evidence
    ):
        raise ManifestValidationError(f"{field}.evidence must contain strings")


def _validate_record_v2(value: object, index: int) -> None:
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
        "operation_group_id",
        "provider_episodes",
        "reason",
    }
    if set(record) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")

    _impl._validate_source(record["source"], f"{field}.source")
    _require_string(record["status"], f"{field}.status")
    status = cast(str, record["status"])
    allowed_statuses = {
        "matched",
        "extra",
        "duplicate",
        "held",
        "suspicious",
        "unresolved",
    }
    if status not in allowed_statuses:
        raise ManifestValidationError(f"{field}.status is not supported")
    _require_string(record["destination"], f"{field}.destination", allow_none=True)
    _require_string(
        record["operation_group_id"],
        f"{field}.operation_group_id",
        allow_none=True,
    )
    _require_string(record["reason"], f"{field}.reason", allow_none=True)
    if record["duplicate"] is not None:
        _validate_duplicate_v2(record["duplicate"], f"{field}.duplicate")

    provider_episodes = record["provider_episodes"]
    if not isinstance(provider_episodes, list | tuple):
        raise ManifestValidationError(f"{field}.provider_episodes must be an array")
    for episode_index, raw_episode in enumerate(provider_episodes):
        episode_field = f"{field}.provider_episodes[{episode_index}]"
        episode = _require_mapping(raw_episode, episode_field)
        expected = {"tvmaze_episode_id", "season", "number", "title", "airdate"}
        if set(episode) != expected:
            raise ManifestValidationError(f"{episode_field} has unexpected fields")
        for key in ("tvmaze_episode_id", "season"):
            current = episode[key]
            if isinstance(current, bool) or not isinstance(current, int):
                raise ManifestValidationError(
                    f"{episode_field}.{key} must be an integer"
                )
        _require_string(episode["title"], f"{episode_field}.title")
        _require_string(episode["airdate"], f"{episode_field}.airdate", allow_none=True)

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
            raise ManifestValidationError(f"{field} matched records require destination")
    if status == "extra" and record["extra"] is None:
        raise ManifestValidationError(f"{field} extra records require extra")
    if status == "duplicate" and record["duplicate"] is None:
        raise ManifestValidationError(f"{field} duplicate records require duplicate")
    if status == "held":
        if record["destination"] is not None:
            raise ManifestValidationError(f"{field} held records must not move")
        if record["extra"] is not None or record["duplicate"] is not None:
            raise ManifestValidationError(
                f"{field} held records cannot be extras or duplicates"
            )
        if provider_episodes:
            raise ManifestValidationError(
                f"{field} held records cannot carry provider episodes"
            )
        if record["evidence"] is None or record["reason"] is None:
            raise ManifestValidationError(
                f"{field} held records require audit evidence"
            )

    if record["evidence"] is not None:
        evidence = _require_mapping(record["evidence"], f"{field}.evidence")
        if "confidence" not in evidence:
            raise ManifestValidationError(f"{field}.evidence.confidence is required")
        _require_number(evidence["confidence"], f"{field}.evidence.confidence")
        evidence_confidence = cast(float, evidence["confidence"])
        if not 0.0 <= evidence_confidence <= 1.0:
            raise ManifestValidationError(
                f"{field}.evidence.confidence must be between 0 and 1"
            )


def _validate_manifest_v2(manifest: Mapping[str, object]) -> None:
    required = {
        "schema_version",
        "overrides_version",
        "provenance",
        "records",
        "companions",
    }
    if set(manifest) != required:
        raise ManifestValidationError("manifest has unexpected fields")
    if manifest["schema_version"] != LEGACY_PLAN_SCHEMA_VERSION:
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
        _validate_record_v2(record, index)

    companions = manifest["companions"]
    if not isinstance(companions, list | tuple):
        raise ManifestValidationError("companions must be an array")
    for index, companion in enumerate(companions):
        _impl._validate_companion(companion, index)

    _impl._validate_provenance(manifest["provenance"])


def validate_manifest(value: object) -> None:
    """Validate immutable v2 manifests or the current v3 manifest contract."""

    manifest = _require_mapping(value, "manifest")
    schema_version = manifest.get("schema_version")
    if schema_version == LEGACY_PLAN_SCHEMA_VERSION:
        _validate_manifest_v2(manifest)
        return
    if schema_version == PLAN_SCHEMA_VERSION:
        _impl.validate_manifest(value)
        return
    raise ManifestValidationError(f"unsupported schema_version: {schema_version!r}")
