from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from importlib.resources import files
from typing import Any, cast

from .models import CompanionPlanRecord, OrganizerPlan, PlanRecord

PLAN_SCHEMA_VERSION = 3
PLAN_SCHEMA_RESOURCE = "data/plan-schema-v3.json"

# Rules mirrored from plan-schema-v2.json $defs, which plan-schema-v3.json reuses.
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_NUMBERING_MODES = frozenset(
    {"aired", "absolute", "parenthesized-absolute", "segment-title", "special", "date"}
)
_SHOW_FIELDS = frozenset({"source_key", "tvmaze_id", "title", "year", "numbering_mode"})
_PARSE_REQUIRED = frozenset(
    {
        "series_hint",
        "season",
        "episodes",
        "absolute_episode",
        "special_kind",
        "special_episode",
        "episode_date",
        "segment_hint",
        "year",
        "embedded_tvmaze_id",
        "title_hint",
    }
)
_PARSE_OPTIONAL = frozenset({"absolute_episodes"})
_EVIDENCE_FIELDS = frozenset({"method", "confidence", "reasons", "candidates"})
_CANDIDATE_FIELDS = frozenset({"tvmaze_id", "title", "score", "reasons"})


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
        if not parse.get("absolute_episodes"):
            parse.pop("absolute_episodes", None)
        parse.pop("series_aliases", None)
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


def _require_integer(
    value: object, field: str, *, minimum: int, allow_none: bool = False
) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestValidationError(f"{field} must be an integer >= {minimum}")


def _require_text(value: object, field: str, *, allow_none: bool = False) -> None:
    """A string that may be empty, matching ``type: string`` without minLength."""

    if value is None and allow_none:
        return
    if not isinstance(value, str):
        raise ManifestValidationError(f"{field} must be a string")


def _require_unit_interval(value: object, field: str) -> None:
    _require_number(value, field)
    if not 0.0 <= cast(float, value) <= 1.0:
        raise ManifestValidationError(f"{field} must be between 0 and 1")


def _require_text_array(value: object, field: str) -> None:
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, str) for item in value
    ):
        raise ManifestValidationError(f"{field} must be an array of strings")


def _require_hex64(value: object, field: str) -> None:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ManifestValidationError(f"{field} must be a 64-character hex digest")


def _validate_show(value: object, field: str) -> None:
    show = _require_mapping(value, field)
    if set(show) != _SHOW_FIELDS:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _require_string(show["source_key"], f"{field}.source_key")
    _require_integer(show["tvmaze_id"], f"{field}.tvmaze_id", minimum=1)
    _require_string(show["title"], f"{field}.title")
    _require_integer(show["year"], f"{field}.year", minimum=1800, allow_none=True)
    if show["numbering_mode"] not in _NUMBERING_MODES:
        raise ManifestValidationError(f"{field}.numbering_mode is not supported")


def _validate_parse(value: object, field: str) -> None:
    parse = _require_mapping(value, field)
    keys = set(parse)
    if not _PARSE_REQUIRED <= keys or not keys <= _PARSE_REQUIRED | _PARSE_OPTIONAL:
        raise ManifestValidationError(f"{field} has unexpected fields")
    for key in ("series_hint", "segment_hint", "title_hint"):
        _require_text(parse[key], f"{field}.{key}", allow_none=True)
    _require_integer(parse["season"], f"{field}.season", minimum=0, allow_none=True)
    episodes = parse["episodes"]
    if not isinstance(episodes, list | tuple):
        raise ManifestValidationError(f"{field}.episodes must be an array")
    for index, episode in enumerate(episodes):
        _require_integer(episode, f"{field}.episodes[{index}]", minimum=0)
    if "absolute_episodes" in parse:
        absolute = parse["absolute_episodes"]
        if not isinstance(absolute, list | tuple):
            raise ManifestValidationError(f"{field}.absolute_episodes must be an array")
        for index, episode in enumerate(absolute):
            _require_integer(episode, f"{field}.absolute_episodes[{index}]", minimum=1)
    _require_integer(
        parse["absolute_episode"],
        f"{field}.absolute_episode",
        minimum=0,
        allow_none=True,
    )
    if parse["special_kind"] not in {"ova", "oad", None}:
        raise ManifestValidationError(f"{field}.special_kind is not supported")
    _require_integer(
        parse["special_episode"],
        f"{field}.special_episode",
        minimum=1,
        allow_none=True,
    )
    episode_date = parse["episode_date"]
    if episode_date is not None and (
        not isinstance(episode_date, str) or _ISO_DATE.fullmatch(episode_date) is None
    ):
        raise ManifestValidationError(f"{field}.episode_date must be YYYY-MM-DD")
    _require_integer(parse["year"], f"{field}.year", minimum=1800, allow_none=True)
    _require_integer(
        parse["embedded_tvmaze_id"],
        f"{field}.embedded_tvmaze_id",
        minimum=1,
        allow_none=True,
    )


def _validate_evidence(value: object, field: str) -> None:
    evidence = _require_mapping(value, field)
    if set(evidence) != _EVIDENCE_FIELDS:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _require_string(evidence["method"], f"{field}.method")
    _require_unit_interval(evidence["confidence"], f"{field}.confidence")
    _require_text_array(evidence["reasons"], f"{field}.reasons")
    candidates = evidence["candidates"]
    if not isinstance(candidates, list | tuple):
        raise ManifestValidationError(f"{field}.candidates must be an array")
    for index, raw_candidate in enumerate(candidates):
        candidate_field = f"{field}.candidates[{index}]"
        candidate = _require_mapping(raw_candidate, candidate_field)
        if set(candidate) != _CANDIDATE_FIELDS:
            raise ManifestValidationError(f"{candidate_field} has unexpected fields")
        _require_integer(
            candidate["tvmaze_id"], f"{candidate_field}.tvmaze_id", minimum=1
        )
        _require_text(candidate["title"], f"{candidate_field}.title")
        _require_unit_interval(candidate["score"], f"{candidate_field}.score")
        _require_text_array(candidate["reasons"], f"{candidate_field}.reasons")


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


def _validate_duplicate(value: object, field: str) -> None:
    duplicate = _require_mapping(value, field)
    required = {
        "destination_key",
        "candidates",
        "winner",
        "losers",
        "confidence",
        "evidence",
        "collision_class",
    }
    if set(duplicate) != required:
        raise ManifestValidationError(f"{field} has unexpected fields")
    _require_string(duplicate["destination_key"], f"{field}.destination_key")
    candidates = duplicate["candidates"]
    if not isinstance(candidates, list | tuple) or len(candidates) < 2:
        raise ManifestValidationError(f"{field}.candidates requires at least two items")
    if not all(isinstance(candidate, str) and candidate for candidate in candidates):
        raise ManifestValidationError(f"{field}.candidates must contain strings")
    candidate_values = cast(Sequence[str], candidates)
    if len(candidate_values) != len(set(candidate_values)):
        raise ManifestValidationError(f"{field}.candidates must be unique")

    winner = duplicate["winner"]
    if winner is not None:
        _require_string(winner, f"{field}.winner")
        if winner not in candidate_values:
            raise ManifestValidationError(f"{field}.winner must be one candidate")
    losers = duplicate["losers"]
    if not isinstance(losers, list | tuple) or not all(
        isinstance(loser, str) and loser for loser in losers
    ):
        raise ManifestValidationError(f"{field}.losers must contain strings")
    loser_values = cast(Sequence[str], losers)
    if len(loser_values) != len(set(loser_values)):
        raise ManifestValidationError(f"{field}.losers must be unique")
    if any(loser not in candidate_values for loser in loser_values):
        raise ManifestValidationError(f"{field}.losers must be candidates")

    _require_number(duplicate["confidence"], f"{field}.confidence")
    confidence = cast(float, duplicate["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ManifestValidationError(f"{field}.confidence must be between 0 and 1")
    evidence = duplicate["evidence"]
    if not isinstance(evidence, list | tuple) or not all(
        isinstance(reason, str) and reason for reason in evidence
    ):
        raise ManifestValidationError(f"{field}.evidence must contain strings")

    collision_class = duplicate["collision_class"]
    if collision_class not in {"same-logical-identity", "destination-conflict"}:
        raise ManifestValidationError(f"{field}.collision_class is not supported")
    if collision_class == "destination-conflict" and (
        winner is not None or loser_values
    ):
        raise ManifestValidationError(
            f"{field} destination conflicts cannot carry a winner or losers"
        )


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
        _validate_duplicate(record["duplicate"], f"{field}.duplicate")

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

    if record["show"] is not None:
        _validate_show(record["show"], f"{field}.show")
    if record["parse"] is not None:
        _validate_parse(record["parse"], f"{field}.parse")
    if record["evidence"] is not None:
        _validate_evidence(record["evidence"], f"{field}.evidence")


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
    if not cast(str, companion["extension"]).startswith("."):
        raise ManifestValidationError(f"{field}.extension must start with '.'")
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
    _require_string(provenance["tool_version"], "provenance.tool_version")
    for key in ("config_snapshot_id", "overrides_snapshot_id"):
        _require_hex64(provenance[key], f"provenance.{key}")
    snapshots = provenance["cache_snapshots"]
    if not isinstance(snapshots, list | tuple):
        raise ManifestValidationError("provenance.cache_snapshots must be an array")
    for index, raw_snapshot in enumerate(snapshots):
        field = f"provenance.cache_snapshots[{index}]"
        snapshot = _require_mapping(raw_snapshot, field)
        expected = {"provider", "kind", "request_key", "snapshot_id", "state"}
        if set(snapshot) != expected:
            raise ManifestValidationError(f"{field} has unexpected fields")
        for key in ("provider", "kind", "request_key", "state"):
            _require_string(snapshot[key], f"{field}.{key}")
        _require_hex64(snapshot["snapshot_id"], f"{field}.snapshot_id")


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
