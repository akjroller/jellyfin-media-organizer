"""Read-only commands that summarize existing audit bundles and review sessions.

inspect, review-status, and report never touch media and never print private
paths unless asked; report writes a path-free, shareable bundle.
"""

from __future__ import annotations

import hashlib
import json
import platform
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from . import __version__
from .privacy import path_free_text
from .review_session import (
    ReviewItemKind,
    ReviewItemState,
    ReviewSession,
    load_review_session,
)
from .summary_io import read_summary, summary_int


def run_inspect(
    run_dir: Path, *, json_output: bool = False, redact_paths: bool = False
) -> int:
    """Summarize an audit bundle without exposing paths or requiring its source tree."""

    root = run_dir.expanduser().resolve(strict=True)
    summary_path = root / "summary.txt"
    if not summary_path.is_file():
        print(f"Inspect failed: {root} does not contain summary.txt")
        return 2
    values = read_summary(summary_path)
    result = {
        "schema_version": 1,
        "readiness_state": values.get("readiness_state", "not-evaluated"),
        "preflight_ready": values.get("preflight_ready", "unknown"),
        "records": summary_int(values, "records"),
        "matched": summary_int(values, "matched"),
        "extra": summary_int(values, "extra"),
        "duplicate": summary_int(values, "duplicate"),
        "held": summary_int(values, "held"),
        "suspicious": summary_int(values, "suspicious"),
        "unresolved": summary_int(values, "unresolved"),
        "remaining_total": summary_int(values, "remaining_total"),
        "skipped_unsupported_videos": summary_int(values, "skipped_unsupported_videos"),
        "skipped_samples": summary_int(values, "skipped_samples"),
        "plan_sha256": values.get("plan_sha256"),
    }
    if not redact_paths:
        result["run_dir"] = str(root)
    if json_output:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        if redact_paths:
            print("Run: [redacted]")
        else:
            print(f"Run: {root}")
        print(f"Status: {result['readiness_state']}")
        for key in (
            "records",
            "matched",
            "extra",
            "duplicate",
            "held",
            "suspicious",
            "unresolved",
            "remaining_total",
        ):
            print(f"{key.replace('_', ' ').title():18} {result[key]}")
        if result["skipped_unsupported_videos"]:
            print(
                f"Unsupported videos {result['skipped_unsupported_videos']} "
                "(not organized; see skipped.csv)"
            )
        if result["readiness_state"] == "apply-ready":
            print("Next step: run jmo apply ... --check-only before any mutation.")
        elif result["duplicate"] or result["held"]:
            print("Next step: run jmo review against this run's plan.json.")
        else:
            print("Next step: inspect preflight.txt and unresolved.csv for blockers.")
    return 0


def run_review_status(
    session_path: Path,
    *,
    json_output: bool = False,
    run_dir: Path | None = None,
    export_path: Path | None = None,
) -> int:
    """Summarize review progress without exposing reviewed source paths."""

    path = session_path.expanduser().resolve(strict=True)
    session = load_review_session(path.read_bytes())
    states = Counter(item.state.value for item in session.items)
    kinds = Counter(item.kind.value for item in session.items)
    total_items = len(session.items)

    def percent(count: int) -> float:
        return round((count / total_items) * 100, 2) if total_items else 0.0

    answered = states[ReviewItemState.ANSWERED.value]
    deferred = states[ReviewItemState.DEFERRED.value]
    pending = states[ReviewItemState.PENDING.value]
    answered_percent = percent(answered)
    deferred_percent = percent(deferred)
    pending_percent = percent(pending)

    plan_summary: dict[str, object] | None = None
    if export_path is not None and run_dir is None:
        raise ValueError("review export requires --run-dir")

    manifest: Mapping[str, object] | None = None
    if run_dir is not None:
        root = run_dir.expanduser().resolve(strict=True)
        summary_path = root / "summary.txt"
        if not summary_path.is_file():
            raise ValueError(
                "review summary run directory does not contain summary.txt"
            )
        values = read_summary(summary_path)

        records = summary_int(values, "records")
        counts = {
            key: summary_int(values, key)
            for key in (
                "matched",
                "extra",
                "duplicate",
                "held",
                "suspicious",
                "unresolved",
                "companions",
                "associated_companions",
                "ignored_companions",
            )
        }
        untouched = sum(
            counts[key] for key in ("duplicate", "held", "suspicious", "unresolved")
        )
        plan_summary = {
            "records": records,
            "counts": counts,
            "percentages": {
                key: round((counts[key] / records) * 100, 2) if records else 0.0
                for key in (
                    "matched",
                    "extra",
                    "duplicate",
                    "held",
                    "suspicious",
                    "unresolved",
                )
            },
            "movable_videos": counts["matched"] + counts["extra"],
            "untouched_videos": untouched,
            "readiness_state": values.get("readiness_state", "not-evaluated"),
            "preflight_ready": values.get("preflight_ready", "unknown"),
        }
        if export_path is not None:
            manifest_path = root / "plan.json"
            if not manifest_path.is_file():
                raise ValueError(
                    "review export run directory does not contain plan.json"
                )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("plan.json root must be an object")
            manifest = payload

    result = {
        "schema_version": 2,
        "session_sha256": session.sha256,
        "plan_sha256": session.plan_sha256,
        "items": total_items,
        "duplicates": kinds[ReviewItemKind.DUPLICATE.value],
        "held": kinds[ReviewItemKind.HELD.value],
        "answered": answered,
        "deferred": deferred,
        "pending": pending,
        "percentages": {
            "answered": answered_percent,
            "deferred": deferred_percent,
            "pending": pending_percent,
        },
        "approved_scope_items": len(session.approved_scope_refs),
        "complete": session.complete,
    }
    if plan_summary is not None:
        result["plan_summary"] = plan_summary
    if export_path is not None:
        assert manifest is not None
        _write_review_export(export_path, session, manifest, result)
    if json_output:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    print("Review status")
    print(f"Items:             {result['items']}")
    print(f"Answered:          {answered} ({answered_percent:.2f}%)")
    print(f"Deferred:           {deferred} ({deferred_percent:.2f}%)")
    print(f"Pending:            {pending} ({pending_percent:.2f}%)")
    if plan_summary is not None:
        plan_counts = plan_summary["counts"]
        assert isinstance(plan_counts, dict)
        print("Plan totals")
        for key in (
            "matched",
            "extra",
            "duplicate",
            "held",
            "suspicious",
            "unresolved",
        ):
            plan_percentages = plan_summary["percentages"]
            assert isinstance(plan_percentages, dict)
            print(
                f"  {key.title():16} {plan_counts[key]} ({plan_percentages[key]:.2f}%)"
            )
        print(f"  {'Companions':16} {plan_counts['companions']}")
        print(
            f"  Associated companions: {plan_counts['associated_companions']}\n"
            f"  Ignored companions:    {plan_counts['ignored_companions']}"
        )
        print(f"  Movable videos:   {plan_summary['movable_videos']}")
        print(f"  Untouched videos: {plan_summary['untouched_videos']}")
        print(
            "  Companions move only when associated with a movable video; ignored "
            "companions remain untouched."
        )
    print("Review categories")
    print(f"  Duplicates:      {result['duplicates']}")
    print(f"  Held:            {result['held']}")
    print(
        "Movement:          review decisions never move media; run a fresh plan and "
        "preflight before any apply."
    )
    print(f"Session SHA-256:   {result['session_sha256']}")
    if session.complete:
        print(
            "Next step: compile a fresh reviewed plan with jmo plan --review-session."
        )
    elif session.approved_partial:
        print(
            "Next step: continue the remaining review scope; partial review never authorizes apply."
        )
    else:
        print("Next step: resume the review session with jmo review --resume.")
    return 0


def _safe_review_value(value: object) -> object:
    if isinstance(value, str):
        return path_free_text(value)
    if isinstance(value, list):
        return [_safe_review_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_review_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if name in {"source", "relative_path", "destination", "winner", "losers"}:
                continue
            if name == "candidates" and isinstance(item, list | tuple):
                result[name] = [
                    (
                        f"candidate-{index}-"
                        f"{hashlib.sha256(str(candidate).encode('utf-8')).hexdigest()[:12]}"
                        if isinstance(candidate, str)
                        else _safe_review_value(candidate)
                    )
                    for index, candidate in enumerate(item, start=1)
                ]
                continue
            result[name] = _safe_review_value(item)
        return result
    return value


def _review_record_by_source(
    manifest: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    records = manifest.get("records")
    if not isinstance(records, list | tuple):
        raise ValueError("plan.json records must be an array")
    result: dict[str, Mapping[str, object]] = {}
    for value in records:
        if not isinstance(value, Mapping):
            continue
        source = value.get("source")
        if isinstance(source, Mapping):
            relative = source.get("relative_path")
            if isinstance(relative, str):
                result[relative.replace("\\", "/").casefold()] = value
    return result


def _write_review_export(
    output_path: Path,
    session: ReviewSession,
    manifest: Mapping[str, object],
    summary: Mapping[str, object],
) -> None:
    """Write an immutable, path-free evidence snapshot for offline review."""

    records = _review_record_by_source(manifest)
    items: list[dict[str, object]] = []
    for item in session.items:
        record: Mapping[str, object] | None = None
        if item.source is not None:
            record = records.get(item.source.replace("\\", "/").casefold())
        item_data: dict[str, object] = {
            "review_ref": item.review_ref,
            "kind": item.kind.value,
            "state": item.state.value,
            "show_key": path_free_text(item.show_key),
            "collision_class": (
                item.collision_class.value if item.collision_class is not None else None
            ),
            "candidate_count": len(item.candidates),
        }
        evidence: dict[str, object] = {}
        if record is not None:
            for key in (
                "parse",
                "show",
                "provider_episodes",
                "evidence",
                "reason",
                "duplicate",
            ):
                if key in record:
                    evidence[key] = _safe_review_value(record[key])
        item_data["evidence"] = evidence
        items.append(item_data)
    document = {
        "schema_version": 1,
        "session_sha256": session.sha256,
        "plan_sha256": session.plan_sha256,
        "complete": session.complete,
        "approved_scope_items": len(session.approved_scope_refs),
        "summary": _safe_review_value(summary),
        "items": sorted(items, key=lambda value: str(value["review_ref"])),
    }
    output = output_path.expanduser().resolve(strict=False)
    if output.exists():
        raise ValueError("review export output already exists")
    if not output.parent.is_dir():
        raise ValueError("review export output parent directory does not exist")
    output.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def run_report(run_dir: Path, output_dir: Path) -> int:
    """Write a path-free, shareable report bundle from one audit directory."""

    root = run_dir.expanduser().resolve(strict=True)
    summary_path = root / "summary.txt"
    if not summary_path.is_file():
        raise ValueError("audit bundle does not contain summary.txt")
    output = output_dir.expanduser().resolve(strict=False)
    if output.exists():
        raise ValueError("report output directory already exists")
    if not output.parent.is_dir():
        raise ValueError("report output parent directory does not exist")

    values = read_summary(summary_path)

    records = summary_int(values, "records")
    counts = {
        key: summary_int(values, key)
        for key in (
            "matched",
            "extra",
            "duplicate",
            "held",
            "suspicious",
            "unresolved",
            "companions",
            "associated_companions",
            "ignored_companions",
            "duplicate_companions",
            "skipped_samples",
            "skipped_unsupported_videos",
        )
    }
    blockers = {key: counts[key] for key in ("suspicious", "unresolved") if counts[key]}
    untouched = (
        counts["duplicate"]
        + counts["held"]
        + counts["suspicious"]
        + counts["unresolved"]
    )
    percentages = {
        key: round((value / records) * 100, 2) if records else 0.0
        for key, value in counts.items()
        if key in {"matched", "extra", "duplicate", "held", "suspicious", "unresolved"}
    }
    companion_percentages = {
        key: round((counts[key] / counts["companions"]) * 100, 2)
        if counts["companions"]
        else 0.0
        for key in (
            "associated_companions",
            "ignored_companions",
            "duplicate_companions",
        )
    }
    payload = {
        "schema_version": 1,
        "tool_version": __version__,
        "platform": platform.platform(aliased=True),
        "python": platform.python_version(),
        "records": records,
        "counts": counts,
        "percentages": percentages,
        "companion_percentages": companion_percentages,
        "movable_videos": counts["matched"] + counts["extra"],
        "untouched_videos": untouched,
        "readiness_state": values.get("readiness_state", "not-evaluated"),
        "preflight_ready": values.get("preflight_ready", "unknown"),
        "failure_categories": blockers,
        "plan_sha256": values.get("plan_sha256"),
    }
    output.mkdir(parents=True)
    (output / "report.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "JMO sanitized audit report",
        f"tool_version={__version__}",
        f"platform={payload['platform']}",
        f"python={payload['python']}",
        f"records={records}",
        f"matched={counts['matched']} ({percentages['matched']}%)",
        f"extra={counts['extra']} ({percentages['extra']}%)",
        f"duplicate={counts['duplicate']} ({percentages['duplicate']}%)",
        f"held={counts['held']} ({percentages['held']}%)",
        f"suspicious={counts['suspicious']} ({percentages['suspicious']}%)",
        f"unresolved={counts['unresolved']} ({percentages['unresolved']}%)",
        f"companions={counts['companions']}",
        f"associated_companions={counts['associated_companions']} ({companion_percentages['associated_companions']}%)",
        f"ignored_companions={counts['ignored_companions']} ({companion_percentages['ignored_companions']}%)",
        f"duplicate_companions={counts['duplicate_companions']} ({companion_percentages['duplicate_companions']}%)",
        f"movable_videos={counts['matched'] + counts['extra']}",
        f"untouched_videos={untouched}",
        f"readiness_state={values.get('readiness_state', 'not-evaluated')}",
        f"preflight_ready={values.get('preflight_ready', 'unknown')}",
        f"failure_categories={json.dumps(blockers, sort_keys=True)}",
        f"plan_sha256={values.get('plan_sha256')}",
        "private_paths=excluded",
        "provider_cache=excluded",
        "credentials=excluded",
        "approval_tokens=excluded",
        "journals=excluded",
    ]
    (output / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Sanitized report written: {output}")
    return 0
