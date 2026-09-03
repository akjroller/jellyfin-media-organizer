from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .decision_hash import stable_decision_hash
from .models import (
    CompanionPlanRecord,
    CompanionStatus,
    DuplicateDecision,
    OrganizerPlan,
    PlanRecord,
    TerminalStatus,
)
from .preflight import PreflightResult, summarize_preflight
from .review import stable_review_ref
from .schema import (
    canonical_companions,
    canonical_manifest_bytes,
    canonical_records,
    stable_plan_hash,
)

_DUPLICATE_REVIEW_FAMILY = "duplicate-review"

CSV_HEADER = (
    "source",
    "review_ref",
    "status",
    "review_family",
    "destination",
    "show_title",
    "provider",
    "provider_id",
    "tvmaze_id",
    "numbering_mode",
    "season",
    "episodes",
    "absolute_episode",
    "match_method",
    "confidence",
    "reasons",
    "extra_kind",
    "duplicate_winner",
    "operation_group_id",
    "provider_episode_ids",
    "reason",
)

DUPLICATE_CSV_HEADER = (
    "duplicate_ref",
    "destination_key",
    "destination",
    "decision_state",
    "candidate_count",
    "candidates",
    "candidate_review_refs",
    "winner",
    "winner_review_ref",
    "losers",
    "loser_review_refs",
    "confidence",
    "evidence",
    "record_statuses",
    "record_sources",
    "operation_group_ids",
)

COMPANION_CSV_HEADER = (
    "source",
    "status",
    "source_video",
    "operation_group_id",
    "destination",
    "kind",
    "reason",
)


@dataclass(frozen=True, slots=True)
class AuditBundle:
    """Immutable rendered audit outputs derived from one finalized plan."""

    plan_json: bytes
    mapping_csv: bytes
    summary_txt: bytes
    plan_sha256: bytes
    decision_sha256: bytes
    unresolved_csv: bytes
    extras_csv: bytes
    duplicates_csv: bytes
    sidecars_csv: bytes
    run_provenance_json: bytes | None = None
    preflight_json: bytes | None = None
    preflight_txt: bytes | None = None

    def files(self) -> tuple[tuple[str, bytes], ...]:
        files = [
            ("mapping.csv", self.mapping_csv),
            ("unresolved.csv", self.unresolved_csv),
            ("extras.csv", self.extras_csv),
            ("duplicates.csv", self.duplicates_csv),
            ("sidecars.csv", self.sidecars_csv),
            ("summary.txt", self.summary_txt),
            ("plan.sha256", self.plan_sha256),
            ("decision.sha256", self.decision_sha256),
        ]
        if self.preflight_json is not None:
            files.append(("preflight.json", self.preflight_json))
        if self.preflight_txt is not None:
            files.append(("preflight.txt", self.preflight_txt))
        if self.run_provenance_json is not None:
            files.append(("run-provenance.json", self.run_provenance_json))
        files.append(("plan.json", self.plan_json))
        return tuple(files)


def _record_review_family(record: PlanRecord) -> str:
    """Return report-only review classification without changing plan status."""

    if (
        record.status is TerminalStatus.SUSPICIOUS
        and record.duplicate is not None
        and record.duplicate.winner is None
    ):
        return _DUPLICATE_REVIEW_FAMILY
    return ""


def _record_row(record: PlanRecord) -> dict[str, str]:
    parse = record.parse
    show = record.show
    evidence = record.evidence
    return {
        "source": record.source.relative_path,
        "review_ref": stable_review_ref(record.source.relative_path),
        "status": record.status.value,
        "review_family": _record_review_family(record),
        "destination": record.destination or "",
        "show_title": show.title if show is not None else "",
        "provider": show.provider if show is not None else "",
        "provider_id": show.provider_id if show is not None else "",
        "tvmaze_id": (
            show.provider_id if show is not None and show.provider == "tvmaze" else ""
        ),
        "numbering_mode": show.numbering_mode.value if show is not None else "",
        "season": str(parse.season)
        if parse is not None and parse.season is not None
        else "",
        "episodes": (
            "|".join(str(episode) for episode in parse.episodes)
            if parse is not None
            else ""
        ),
        "absolute_episode": (
            str(parse.absolute_episode)
            if parse is not None and parse.absolute_episode is not None
            else ""
        ),
        "match_method": evidence.method if evidence is not None else "",
        "confidence": (
            format(evidence.confidence, ".6g") if evidence is not None else ""
        ),
        "reasons": "; ".join(evidence.reasons) if evidence is not None else "",
        "extra_kind": record.extra.kind if record.extra is not None else "",
        "duplicate_winner": (
            record.duplicate.winner
            if record.duplicate is not None and record.duplicate.winner is not None
            else ""
        ),
        "operation_group_id": record.operation_group_id or "",
        "provider_episode_ids": "|".join(
            episode.provider_id for episode in record.provider_episodes
        ),
        "reason": record.reason or "",
    }


def _render_record_csv(records: tuple[PlanRecord, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_HEADER, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(_record_row(record))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _duplicate_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.replace("\\", "/"))
    return normalized.casefold()


def stable_duplicate_ref(decision: DuplicateDecision) -> str:
    """Return a stable local review reference for one duplicate collision."""

    identity = {
        "destination_key": _duplicate_key(decision.destination_key),
        "candidates": sorted(_duplicate_key(value) for value in decision.candidates),
    }
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"duplicate-{digest}"


def _duplicate_groups(
    plan: OrganizerPlan,
) -> tuple[tuple[DuplicateDecision, tuple[PlanRecord, ...]], ...]:
    grouped: dict[str, tuple[DuplicateDecision, list[PlanRecord]]] = {}
    for record in canonical_records(plan):
        decision = record.duplicate
        if decision is None:
            continue
        key = _duplicate_key(decision.destination_key)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = decision, [record]
            continue
        existing_decision, records = existing
        if existing_decision != decision:
            raise ValueError(
                "conflicting duplicate decisions for destination key: "
                f"{decision.destination_key}"
            )
        records.append(record)

    return tuple(
        (decision, tuple(records))
        for _key, (decision, records) in sorted(grouped.items())
    )


def _duplicate_row(
    decision: DuplicateDecision,
    records: tuple[PlanRecord, ...],
) -> dict[str, str]:
    candidates = decision.candidates
    losers = decision.losers
    destinations = sorted(
        {record.destination for record in records if record.destination is not None},
        key=lambda value: (_duplicate_key(value), value),
    )
    statuses = sorted({record.status.value for record in records})
    sources = tuple(record.source.relative_path for record in records)
    operation_group_ids = sorted(
        {
            record.operation_group_id
            for record in records
            if record.operation_group_id is not None
        }
    )
    return {
        "duplicate_ref": stable_duplicate_ref(decision),
        "destination_key": decision.destination_key,
        "destination": "|".join(destinations),
        "decision_state": "winner-selected"
        if decision.winner is not None
        else "review-required",
        "candidate_count": str(len(candidates)),
        "candidates": "|".join(candidates),
        "candidate_review_refs": "|".join(
            stable_review_ref(value) for value in candidates
        ),
        "winner": decision.winner or "",
        "winner_review_ref": (
            stable_review_ref(decision.winner) if decision.winner is not None else ""
        ),
        "losers": "|".join(losers),
        "loser_review_refs": "|".join(stable_review_ref(value) for value in losers),
        "confidence": format(decision.confidence, ".6g"),
        "evidence": "; ".join(decision.evidence),
        "record_statuses": "|".join(statuses),
        "record_sources": "|".join(sources),
        "operation_group_ids": "|".join(operation_group_ids),
    }


def render_duplicates_csv(plan: OrganizerPlan) -> bytes:
    """Render one deterministic spreadsheet row per duplicate collision group."""

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=DUPLICATE_CSV_HEADER,
        lineterminator="\n",
    )
    writer.writeheader()
    for decision, records in _duplicate_groups(plan):
        writer.writerow(_duplicate_row(decision, records))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _companion_row(record: CompanionPlanRecord) -> dict[str, str]:
    return {
        "source": record.relative_path,
        "status": record.status.value,
        "source_video": record.source_video or "",
        "operation_group_id": record.operation_group_id or "",
        "destination": record.destination or "",
        "kind": record.kind or "",
        "reason": record.reason,
    }


def render_sidecars_csv(plan: OrganizerPlan) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=COMPANION_CSV_HEADER,
        lineterminator="\n",
    )
    writer.writeheader()
    for record in canonical_companions(plan):
        writer.writerow(_companion_row(record))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def render_mapping_csv(plan: OrganizerPlan) -> bytes:
    """Render a stable UTF-8-with-BOM spreadsheet-friendly mapping report."""

    return _render_record_csv(canonical_records(plan))


def render_summary(
    plan: OrganizerPlan,
    preflight: PreflightResult | None = None,
) -> bytes:
    """Render a concise, path-free status summary tied to both plan fingerprints."""

    counts = Counter(record.status for record in plan.records)
    duplicate_review = sum(
        _record_review_family(record) == _DUPLICATE_REVIEW_FAMILY
        for record in plan.records
    )
    lines = [
        f"plan_sha256={stable_plan_hash(plan)}",
        f"decision_sha256={stable_decision_hash(plan)}",
        f"records={len(plan.records)}",
    ]
    lines.extend(f"{status.value}={counts[status]}" for status in TerminalStatus)
    lines.extend(
        (
            f"duplicate_review={duplicate_review}",
            "suspicious_excluding_duplicate_review="
            f"{counts[TerminalStatus.SUSPICIOUS] - duplicate_review}",
        )
    )
    companion_counts = Counter(record.status for record in plan.companions)
    lines.append(f"companions={len(plan.companions)}")
    lines.extend(
        f"companion_{status.value}={companion_counts[status]}"
        for status in CompanionStatus
    )
    if preflight is not None:
        lines.append(f"preflight_ready={str(preflight.ready).lower()}")
        lines.append(f"preflight_findings={len(preflight.findings)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_audit_bundle(
    plan: OrganizerPlan,
    preflight: PreflightResult | None = None,
    *,
    run_provenance_json: bytes | None = None,
) -> AuditBundle:
    """Render every public audit artifact from the same immutable plan object."""

    plan_hash = stable_plan_hash(plan)
    decision_hash = stable_decision_hash(plan)
    records = canonical_records(plan)
    unresolved = tuple(
        record
        for record in records
        if record.status in {TerminalStatus.SUSPICIOUS, TerminalStatus.UNRESOLVED}
    )
    extras = tuple(record for record in records if record.extra is not None)
    preflight_json = None
    preflight_txt = None
    if preflight is not None:
        preflight_json = (
            json.dumps(
                preflight.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        preflight_txt = (summarize_preflight(preflight) + "\n").encode("utf-8")

    return AuditBundle(
        plan_json=canonical_manifest_bytes(plan) + b"\n",
        mapping_csv=_render_record_csv(records),
        unresolved_csv=_render_record_csv(unresolved),
        extras_csv=_render_record_csv(extras),
        duplicates_csv=render_duplicates_csv(plan),
        sidecars_csv=render_sidecars_csv(plan),
        run_provenance_json=run_provenance_json,
        summary_txt=render_summary(plan, preflight),
        plan_sha256=f"{plan_hash}\n".encode("ascii"),
        decision_sha256=f"{decision_hash}\n".encode("ascii"),
        preflight_json=preflight_json,
        preflight_txt=preflight_txt,
    )


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_audit_bundle(
    output_dir: Path,
    plan: OrganizerPlan,
    preflight: PreflightResult | None = None,
    *,
    run_provenance_json: bytes | None = None,
) -> AuditBundle:
    """Publish an audit bundle without leaving a half-valid plan on failure.

    The output directory is created with ``exist_ok=False``. Report files are
    written atomically, and ``plan.json`` is published last as the completion
    marker. Any ordinary exception removes the newly created output directory.
    """

    bundle = render_audit_bundle(
        plan, preflight, run_provenance_json=run_provenance_json
    )
    output_dir.mkdir(parents=False, exist_ok=False)
    try:
        for name, data in bundle.files():
            _atomic_write(output_dir / name, data)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return bundle
