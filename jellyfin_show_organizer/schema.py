from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from importlib.resources import files
from typing import Any, cast

from .models import OrganizerPlan

PLAN_SCHEMA_VERSION = 1
PLAN_SCHEMA_RESOURCE = "data/plan-schema-v1.json"
_PROVIDER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ManifestValidationError(ValueError):
    """Raised when serialized plan data does not satisfy the current schema."""


def load_plan_schema() -> dict[str, Any]:
    resource = files("jellyfin_show_organizer").joinpath(PLAN_SCHEMA_RESOURCE)
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


def plan_to_manifest(plan: OrganizerPlan) -> dict[str, Any]:
    payload = cast(
        dict[str, Any],
        json.loads(json.dumps(asdict(plan), ensure_ascii=False)),
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


def _validate_provider_identity(value: object, field: str) -> None:
    identity = _require_mapping(value, field)
    required = {"provider", "value"}
    if set(identity) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _require_string(identity["provider"], f"{field}.provider")
    _require_string(identity["value"], f"{field}.value")
    provider = cast(str, identity["provider"])
    if _PROVIDER_NAME.fullmatch(provider) is None:
        raise ManifestValidationError(f"{field}.provider is not normalized")


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


def _validate_parse(value: object, field: str) -> None:
    if value is None:
        return
    parse = _require_mapping(value, field)
    required = {
        "series_hint",
        "season",
        "episodes",
        "absolute_episode",
        "segment_hint",
        "year",
        "embedded_tvmaze_id",
        "title_hint",
        "embedded_provider_identity",
    }
    if set(parse) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    for key in ("series_hint", "segment_hint", "title_hint"):
        current = parse[key]
        if current is not None and not isinstance(current, str):
            raise ManifestValidationError(f"{field}.{key} must be a string or null")
    for key in ("season", "absolute_episode"):
        current = parse[key]
        if current is not None and (
            isinstance(current, bool) or not isinstance(current, int) or current < 0
        ):
            raise ManifestValidationError(f"{field}.{key} must be non-negative or null")
    episodes = parse["episodes"]
    if not isinstance(episodes, list | tuple) or any(
        isinstance(episode, bool) or not isinstance(episode, int) or episode < 0
        for episode in episodes
    ):
        raise ManifestValidationError(f"{field}.episodes must be non-negative integers")
    year = parse["year"]
    if year is not None and (
        isinstance(year, bool) or not isinstance(year, int) or year < 1800
    ):
        raise ManifestValidationError(f"{field}.year is outside the supported range")
    tvmaze_id = parse["embedded_tvmaze_id"]
    if tvmaze_id is not None and (
        isinstance(tvmaze_id, bool) or not isinstance(tvmaze_id, int) or tvmaze_id <= 0
    ):
        raise ManifestValidationError(f"{field}.embedded_tvmaze_id must be positive")
    embedded_identity = parse["embedded_provider_identity"]
    if embedded_identity is not None:
        _validate_provider_identity(
            embedded_identity,
            f"{field}.embedded_provider_identity",
        )


def _validate_show(value: object, field: str) -> None:
    if value is None:
        return
    show = _require_mapping(value, field)
    required = {"source_key", "provider_identity", "title", "year", "numbering_mode"}
    if set(show) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _require_string(show["source_key"], f"{field}.source_key")
    _validate_provider_identity(show["provider_identity"], f"{field}.provider_identity")
    _require_string(show["title"], f"{field}.title")
    year = show["year"]
    if year is not None and (
        isinstance(year, bool) or not isinstance(year, int) or year < 1800
    ):
        raise ManifestValidationError(f"{field}.year is outside the supported range")
    if show["numbering_mode"] not in {
        "aired",
        "absolute",
        "parenthesized-absolute",
        "segment-title",
    }:
        raise ManifestValidationError(f"{field}.numbering_mode is not supported")


def _validate_candidate(value: object, field: str) -> None:
    candidate = _require_mapping(value, field)
    required = {"provider_identity", "title", "score", "reasons"}
    if set(candidate) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _validate_provider_identity(
        candidate["provider_identity"],
        f"{field}.provider_identity",
    )
    _require_string(candidate["title"], f"{field}.title")
    _require_number(candidate["score"], f"{field}.score")
    score = cast(float, candidate["score"])
    if not 0.0 <= score <= 1.0:
        raise ManifestValidationError(f"{field}.score must be between 0 and 1")
    reasons = candidate["reasons"]
    if not isinstance(reasons, list | tuple) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ManifestValidationError(f"{field}.reasons must be an array of strings")


def _validate_evidence(value: object, field: str) -> None:
    if value is None:
        return
    evidence = _require_mapping(value, field)
    required = {"method", "confidence", "reasons", "candidates"}
    if set(evidence) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _require_string(evidence["method"], f"{field}.method")
    _require_number(evidence["confidence"], f"{field}.confidence")
    confidence = cast(float, evidence["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ManifestValidationError(f"{field}.confidence must be between 0 and 1")
    reasons = evidence["reasons"]
    if not isinstance(reasons, list | tuple) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ManifestValidationError(f"{field}.reasons must be an array of strings")
    candidates = evidence["candidates"]
    if not isinstance(candidates, list | tuple):
        raise ManifestValidationError(f"{field}.candidates must be an array")
    for index, candidate in enumerate(candidates):
        _validate_candidate(candidate, f"{field}.candidates[{index}]")


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
    _validate_parse(record["parse"], f"{field}.parse")
    _validate_show(record["show"], f"{field}.show")
    _validate_evidence(record["evidence"], f"{field}.evidence")

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
