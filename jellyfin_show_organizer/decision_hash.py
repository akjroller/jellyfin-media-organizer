from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .models import CompanionPlanRecord, OrganizerPlan, ParseResult, PlanRecord
from .schema import canonical_companions, canonical_records

DECISION_HASH_VERSION = 1


def _duplicate_decision(record: PlanRecord) -> dict[str, Any] | None:
    duplicate = record.duplicate
    if duplicate is None:
        return None
    return {
        "destination_key": duplicate.destination_key,
        "candidates": list(duplicate.candidates),
        "winner": duplicate.winner,
        "losers": list(duplicate.losers),
    }


def _parse_decision(parse: ParseResult | None) -> dict[str, Any] | None:
    if parse is None:
        return None
    payload = asdict(parse)
    payload.pop("series_aliases", None)
    return payload


def _record_decision(record: PlanRecord) -> dict[str, Any]:
    """Return only outcome-bearing fields for one video record.

    Match evidence, explanatory reasons, confidence scores, and provider/cache
    provenance are deliberately excluded. The exact plan hash remains the only
    approval/apply identity; this payload exists solely to compare whether two
    planning runs reached the same operational decisions.
    """

    return {
        "source": asdict(record.source),
        "status": record.status.value,
        "parse": _parse_decision(record.parse),
        "show": asdict(record.show) if record.show is not None else None,
        "destination": record.destination,
        "extra": asdict(record.extra) if record.extra is not None else None,
        "duplicate": _duplicate_decision(record),
        "operation_group_id": record.operation_group_id,
        "provider_episodes": [asdict(episode) for episode in record.provider_episodes],
    }


def _companion_decision(record: CompanionPlanRecord) -> dict[str, Any]:
    return {
        "relative_path": record.relative_path,
        "extension": record.extension,
        "fingerprint": (
            asdict(record.fingerprint) if record.fingerprint is not None else None
        ),
        "status": record.status.value,
        "source_video": record.source_video,
        "operation_group_id": record.operation_group_id,
        "destination": record.destination,
        "kind": record.kind,
    }


def decision_manifest_bytes(plan: OrganizerPlan) -> bytes:
    """Serialize a deterministic provenance-independent decision fingerprint."""

    payload = {
        "decision_hash_version": DECISION_HASH_VERSION,
        "records": [_record_decision(record) for record in canonical_records(plan)],
        "companions": [
            _companion_decision(record) for record in canonical_companions(plan)
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def stable_decision_hash(plan: OrganizerPlan) -> str:
    """Hash planning outcomes without weakening the exact provenance-bound hash."""

    return hashlib.sha256(decision_manifest_bytes(plan)).hexdigest()
