from __future__ import annotations

import csv
import io
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .models import OrganizerPlan, PlanRecord, TerminalStatus
from .schema import canonical_manifest_bytes, canonical_records, stable_plan_hash

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
)


@dataclass(frozen=True, slots=True)
class AuditBundle:
    """Immutable rendered audit outputs derived from one finalized plan."""

    plan_json: bytes
    mapping_csv: bytes
    summary_txt: bytes
    plan_sha256: bytes

    def files(self) -> tuple[tuple[str, bytes], ...]:
        return (
            ("mapping.csv", self.mapping_csv),
            ("summary.txt", self.summary_txt),
            ("plan.sha256", self.plan_sha256),
            ("plan.json", self.plan_json),
        )


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
        "season": str(parse.season) if parse is not None and parse.season is not None else "",
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
    }


def render_mapping_csv(plan: OrganizerPlan) -> bytes:
    """Render a stable UTF-8-with-BOM spreadsheet-friendly mapping report."""

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_HEADER, lineterminator="\n")
    writer.writeheader()
    for record in canonical_records(plan):
        writer.writerow(_record_row(record))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def render_summary(plan: OrganizerPlan) -> bytes:
    """Render a concise, path-free status summary tied to the exact plan hash."""

    counts = Counter(record.status for record in plan.records)
    lines = [
        f"plan_sha256={stable_plan_hash(plan)}",
        f"records={len(plan.records)}",
    ]
    lines.extend(f"{status.value}={counts[status]}" for status in TerminalStatus)
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_audit_bundle(plan: OrganizerPlan) -> AuditBundle:
    """Render every public audit artifact from the same immutable plan object."""

    plan_hash = stable_plan_hash(plan)
    return AuditBundle(
        plan_json=canonical_manifest_bytes(plan) + b"\n",
        mapping_csv=render_mapping_csv(plan),
        summary_txt=render_summary(plan),
        plan_sha256=f"{plan_hash}\n".encode("ascii"),
    )


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_audit_bundle(output_dir: Path, plan: OrganizerPlan) -> AuditBundle:
    """Publish an audit bundle without leaving a half-valid plan on failure.

    The output directory is created with ``exist_ok=False``. Report files are
    written atomically, and ``plan.json`` is published last as the completion
    marker. Any ordinary exception removes the newly created output directory.
    """

    bundle = render_audit_bundle(plan)
    output_dir.mkdir(parents=False, exist_ok=False)
    try:
        for name, data in bundle.files():
            _atomic_write(output_dir / name, data)
    except BaseException:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return bundle
