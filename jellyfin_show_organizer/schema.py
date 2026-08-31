from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict
from importlib.resources import files
from typing import Any, cast

from .models import CompanionPlanRecord, OrganizerPlan, PlanRecord

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
        sorted(
            plan.records, key=lambda record: _path_sort_key(record.source.relative_path)
        )
    )


def canonical_companions(plan: OrganizerPlan) -> tuple[CompanionPlanRecord, ...]:
    """Return companion records in a platform-independent deterministic order."""

    return tuple(
        sorted(
            plan.companions,
            key=lambda record: _path_sort_key(record.relative_path),
        )
    )


def _legacy_tvmaze_id(value: object, field: str) -> int:
    if not isinstance(value, Mapping):
        raise ManifestValidationError(f"{field} must be a provider identity")
    identity = cast(Mapping[str, object], value)
    if set(identity) != {"provider", "value"}:
        raise ManifestValidationError(f"{field} has unexpected fields")
    if identity["provider"] != "tvmaze":
        raise ManifestValidationError(
            f"{field} cannot be represented by plan schema v1"
        )
    raw_value = identity["value"]
    if not isinstance(raw_value, str):
        raise ManifestValidationError(f"{field}.value must be a string")
    try:
        numeric = int(raw_value)
    except ValueError as exc:
        raise ManifestValidationError(
            f"{field}.value must be a canonical positive integer"
        ) from exc
    if numeric <= 0 or str(numeric) != raw_value:
        raise ManifestValidationError(
            f"{field}.value must be a canonical positive integer"
        )
    return numeric


def _serialize_record_v1(record: PlanRecord) -> dict[str, Any]:
    payload = cast(dict[str, Any], asdict(record))

    parse = payload.get("parse")
    if isinstance(parse, dict):
        embedded = parse.pop("embedded_provider_identity", None)
        if embedded is not None:
            numeric = _legacy_tvmaze_id(
                embedded,
                "parse.embedded_provider_identity",
            )
            existing = parse.get("embedded_tvmaze_id")
            if existing is not None and existing != numeric:
                raise ManifestValidationError(
                    "parse contains conflicting provider identities"
                )
            parse["embedded_tvmaze_id"] = numeric

    show = payload.get("show")
    if isinstance(show, dict):
        identity = show.pop("provider_identity", None)
        if identity is None:
            raise ManifestValidationError("show provider identity is required")
        show["tvmaze_id"] = _legacy_tvmaze_id(
            identity,
            "show.provider_identity",
        )

    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        candidates = evidence.get("candidates", [])
        if not isinstance(candidates, list | tuple):
            raise ManifestValidationError("evidence.candidates must be an array")
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                raise ManifestValidationError(
                    f"evidence.candidates[{index}] must be an object"
                )
            identity = candidate.pop("provider_identity", None)
            if identity is None:
                raise ManifestValidationError(
                    f"evidence.candidates[{index}] provider identity is required"
                )
            candidate["tvmaze_id"] = _legacy_tvmaze_id(
                identity,
                f"evidence.candidates[{index}].provider_identity",
            )

    provider_episodes = payload.get("provider_episodes", [])
    if not isinstance(provider_episodes, list | tuple):
        raise ManifestValidationError("provider_episodes must be an array")
    for index, episode in enumerate(provider_episodes):
        if not isinstance(episode, dict):
            raise ManifestValidationError(
                f"provider_episodes[{index}] must be an object"
            )
        identity = episode.pop("provider_identity", None)
        if identity is None:
            raise ManifestValidationError(
                f"provider_episodes[{index}] provider identity is required"
            )
        episode["tvmaze_episode_id"] = _legacy_tvmaze_id(
            identity,
            f"provider_episodes[{index}].provider_identity",
        )

    return payload


def plan_to_manifest(plan: OrganizerPlan) -> dict[str, Any]:
    provenance = asdict(plan.provenance) if plan.provenance is not None else None
    if provenance is not None:
        provenance["cache_snapshots"] = sorted(
            provenance["cache_snapshots"],
            key=lambda item: (
                item["provider"],
                item["kind"],
                item["request_key"],
                item["snapshot_id"],
            ),
        )
    payload = cast(
        dict[str, Any],
        json.loads(
            json.dumps(
                {
                    "schema_version": plan.schema_version,
                    "overrides_version": plan.overrides_version,
                    "provenance": provenance,
                    "records": [
                        _serialize_record_v1(record)
                        for record in canonical_records(plan)
                    ],
                    "companions": [
                        asdict(record) for record in canonical_companions(plan)
                    ],
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
        "operation_group_id",
        "provider_episodes",
        "reason",
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
    _require_string(
        record["operation_group_id"],
        f"{field}.operation_group_id",
        allow_none=True,
    )
    _require_string(record["reason"], f"{field}.reason", allow_none=True)

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


def _validate_companion(value: object, index: int) -> None:
    field = f"companions[{index}]"
    companion = _require_mapping(value, field)
    required = {
        "relative_path",
        "extension",
        "fingerprint",
        "status",
        "reason",
        "source_video",
        "operation_group_id",
        "destination",
        "kind",
    }
    if set(companion) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    for key in ("relative_path", "extension", "status", "reason"):
        _require_string(companion[key], f"{field}.{key}")
    for key in ("source_video", "operation_group_id", "destination", "kind"):
        _require_string(companion[key], f"{field}.{key}", allow_none=True)
    if companion["fingerprint"] is not None:
        _validate_fingerprint(companion["fingerprint"], f"{field}.fingerprint")
    allowed = {"associated", "duplicate", "ignored", "unresolved"}
    if companion["status"] not in allowed:
        raise ManifestValidationError(f"{field}.status is not supported")


def _validate_provenance(value: object) -> None:
    if value is None:
        return
    provenance = _require_mapping(value, "provenance")
    required = {
        "tool_version",
        "config_snapshot_id",
        "overrides_snapshot_id",
        "cache_snapshots",
    }
    if set(provenance) != required:
        raise ManifestValidationError("provenance has unexpected fields")
    for key in ("tool_version", "config_snapshot_id", "overrides_snapshot_id"):
        _require_string(provenance[key], f"provenance.{key}")
    snapshots = provenance["cache_snapshots"]
    if not isinstance(snapshots, list | tuple):
        raise ManifestValidationError("provenance.cache_snapshots must be an array")
    for index, raw_snapshot in enumerate(snapshots):
        field = f"provenance.cache_snapshots[{index}]"
        snapshot = _require_mapping(raw_snapshot, field)
        expected = {"provider", "kind", "request_key", "snapshot_id", "state"}
        if set(snapshot) != expected:
            raise ManifestValidationError(f"{field} has unexpected fields")
        for key in expected:
            _require_string(snapshot[key], f"{field}.{key}")


def validate_manifest(value: object) -> None:
    manifest = _require_mapping(value, "manifest")
    required = {
        "schema_version",
        "overrides_version",
        "provenance",
        "records",
        "companions",
    }
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

    companions = manifest["companions"]
    if not isinstance(companions, list | tuple):
        raise ManifestValidationError("companions must be an array")
    for index, companion in enumerate(companions):
        _validate_companion(companion, index)

    _validate_provenance(manifest["provenance"])
