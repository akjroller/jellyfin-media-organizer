from __future__ import annotations

import csv
import io
import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .models import (
    CompanionPlanRecord,
    CompanionStatus,
    OrganizerPlan,
    PlanRecord,
    TerminalStatus,
)
from .preflight import PreflightResult, summarize_preflight
from .schema import (
    canonical_companions,
    canonical_manifest_bytes,
    canonical_records,
    stable_plan_hash,
)

CSV_HEADER = (
    "source",
    "status",
    "destination",
    "show_title",
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
    unresolved_csv: bytes
    extras_csv: bytes
    duplicates_csv: bytes
    sidecars_csv: bytes
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
        ]
        if self.preflight_json is not None:
            files.append(("preflight.json", self.preflight_json))
        if self.preflight_txt is not None:
            files.append(("preflight.txt", self.preflight_txt))
        files.append(("plan.json", self.plan_json))
        return tuple(files)


def _record_row(record: PlanRecord) -> dict[str, str]:
    parse = record.parse
    show = record.show
    evidence = record.evidence
    return {
        "source": record.source.relative_path,
        "status": record.status.value,
        "destination": record.destination or "",
        "show_title": show.title if show is not None else "",
        "tvmaze_id": str(show.tvmaze_id) if show is not None else "",
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
            str(episode.tvmaze_episode_id) for episode in record.provider_episodes
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
    """Render a concise, path-free status summary tied to the exact plan hash."""

    counts = Counter(record.status for record in plan.records)
    lines = [
        f"plan_sha256={stable_plan_hash(plan)}",
        f"records={len(plan.records)}",
    ]
    lines.extend(f"{status.value}={counts[status]}" for status in TerminalStatus)
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
) -> AuditBundle:
    """Render every public audit artifact from the same immutable plan object."""

    plan_hash = stable_plan_hash(plan)
    records = canonical_records(plan)
    unresolved = tuple(
        record
        for record in records
        if record.status in {TerminalStatus.SUSPICIOUS, TerminalStatus.UNRESOLVED}
    )
    extras = tuple(record for record in records if record.extra is not None)
    duplicates = tuple(record for record in records if record.duplicate is not None)
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
        duplicates_csv=_render_record_csv(duplicates),
        sidecars_csv=render_sidecars_csv(plan),
        summary_txt=render_summary(plan, preflight),
        plan_sha256=f"{plan_hash}\n".encode("ascii"),
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
) -> AuditBundle:
    """Publish an audit bundle without leaving a half-valid plan on failure.

    The output directory is created with ``exist_ok=False``. Report files are
    written atomically, and ``plan.json`` is published last as the completion
    marker. Any ordinary exception removes the newly created output directory.
    """

    bundle = render_audit_bundle(plan, preflight)
    output_dir.mkdir(parents=False, exist_ok=False)
    try:
        for name, data in bundle.files():
            _atomic_write(output_dir / name, data)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return bundle
