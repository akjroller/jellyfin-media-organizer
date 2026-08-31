from __future__ import annotations

from pathlib import Path

import pytest

from jellyfin_show_organizer import reports
from jellyfin_show_organizer.models import (
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.reports import render_audit_bundle, write_audit_bundle
from jellyfin_show_organizer.schema import canonical_manifest_bytes, stable_plan_hash

pytestmark = pytest.mark.local


def _source(relative_path: str, *, size: int) -> SourceFile:
    return SourceFile(
        relative_path=relative_path,
        extension=".mkv",
        fingerprint=SourceFingerprint(size=size, mtime_ns=123456789),
    )


def _matched_record(relative_path: str, *, episode: int, size: int) -> PlanRecord:
    return PlanRecord(
        source=_source(relative_path, size=size),
        status=TerminalStatus.MATCHED,
        parse=ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(episode,),
        ),
        show=CanonicalShow(
            source_key="example-series",
            tvmaze_id=12345,
            title="Example Series",
            year=2000,
            numbering_mode=NumberingMode.AIRED,
        ),
        evidence=MatchEvidence(
            method="synthetic-test-match",
            confidence=1.0,
            reasons=("fabricated evidence",),
        ),
        destination=(
            f"Example Series/Season 01/Example Series S01E{episode:02d}.mkv"
        ),
    )


def _plan(*records: PlanRecord) -> OrganizerPlan:
    return OrganizerPlan(schema_version=1, overrides_version=1, records=records)


def test_canonical_plan_and_reports_ignore_record_insertion_order():
    first_record = _matched_record(
        "Example Series/Example Series S01E01.mkv",
        episode=1,
        size=100,
    )
    second_record = _matched_record(
        "Example Series/Example Series S01E02.mkv",
        episode=2,
        size=200,
    )
    forward = _plan(first_record, second_record)
    reversed_plan = _plan(second_record, first_record)

    assert canonical_manifest_bytes(forward) == canonical_manifest_bytes(reversed_plan)
    assert stable_plan_hash(forward) == stable_plan_hash(reversed_plan)
    assert render_audit_bundle(forward) == render_audit_bundle(reversed_plan)


def test_audit_bundle_uses_utf8_json_and_bom_csv():
    bundle = render_audit_bundle(
        _plan(
            _matched_record(
                "Example Series/Épisode S01E01.mkv",
                episode=1,
                size=100,
            )
        )
    )

    assert bundle.plan_json.startswith(b"{")
    assert not bundle.plan_json.startswith(b"\xef\xbb\xbf")
    assert "Épisode" in bundle.plan_json.decode("utf-8")
    assert bundle.mapping_csv.startswith(b"\xef\xbb\xbf")
    assert "Épisode" in bundle.mapping_csv.decode("utf-8-sig")


def test_summary_is_path_free_and_tied_to_plan_hash():
    plan = _plan(
        _matched_record(
            "Private Looking Folder/Example Series S01E01.mkv",
            episode=1,
            size=100,
        ),
        PlanRecord(
            source=_source("Other Folder/Unknown.mkv", size=50),
            status=TerminalStatus.UNRESOLVED,
        ),
    )

    summary = render_audit_bundle(plan).summary_txt.decode("utf-8")

    assert f"plan_sha256={stable_plan_hash(plan)}" in summary
    assert "records=2" in summary
    assert "matched=1" in summary
    assert "unresolved=1" in summary
    assert "Private Looking Folder" not in summary
    assert "Other Folder" not in summary


def test_write_audit_bundle_publishes_plan_json_last_and_matches_rendered_bytes(
    tmp_path: Path,
):
    plan = _plan(
        _matched_record(
            "Example Series/Example Series S01E01.mkv",
            episode=1,
            size=100,
        )
    )
    output_dir = tmp_path / "audit"

    bundle = write_audit_bundle(output_dir, plan)

    assert (output_dir / "mapping.csv").read_bytes() == bundle.mapping_csv
    assert (output_dir / "summary.txt").read_bytes() == bundle.summary_txt
    assert (output_dir / "plan.sha256").read_bytes() == bundle.plan_sha256
    assert (output_dir / "plan.json").read_bytes() == bundle.plan_json


def test_write_audit_bundle_fails_closed_when_output_directory_exists(tmp_path: Path):
    output_dir = tmp_path / "audit"
    output_dir.mkdir()
    sentinel = output_dir / "keep.txt"
    sentinel.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_audit_bundle(output_dir, _plan())

    assert sentinel.read_text(encoding="utf-8") == "existing"
    assert sorted(path.name for path in output_dir.iterdir()) == ["keep.txt"]


def test_write_failure_removes_partial_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output_dir = tmp_path / "audit"
    original_write = reports._atomic_write

    def fail_on_summary(path: Path, data: bytes) -> None:
        if path.name == "summary.txt":
            raise OSError("synthetic report failure")
        original_write(path, data)

    monkeypatch.setattr(reports, "_atomic_write", fail_on_summary)

    with pytest.raises(OSError, match="synthetic report failure"):
        write_audit_bundle(output_dir, _plan())

    assert not output_dir.exists()
