from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from jellyfin_show_organizer.cli import main
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
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.reports import render_audit_bundle
from jellyfin_show_organizer.review import render_override_stub, stable_review_ref
from jellyfin_show_organizer.schema import plan_to_manifest

pytestmark = pytest.mark.local


def _source(path: str) -> SourceFile:
    return SourceFile(
        relative_path=path,
        extension=".mkv",
        fingerprint=SourceFingerprint(size=1, mtime_ns=2),
    )


def _review_plan() -> OrganizerPlan:
    evidence = MatchEvidence(
        method="fabricated-review",
        confidence=0.0,
        reasons=("ambiguous fabricated provider candidates",),
    )
    show = CanonicalShow(
        source_key="Fabricated Series",
        tvmaze_id=4242,
        title="Fabricated Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )
    return OrganizerPlan(
        schema_version=1,
        overrides_version=2,
        records=(
            PlanRecord(
                source=_source("Fabricated Series/Fabricated.Series.S01E01.mkv"),
                status=TerminalStatus.UNRESOLVED,
                parse=ParseResult(
                    series_hint="Fabricated Series",
                    season=1,
                    episodes=(1,),
                    year=2024,
                ),
                evidence=evidence,
                operation_group_id="op-fabricated-one",
                reason="ambiguous fabricated provider candidates",
            ),
            PlanRecord(
                source=_source("Fabricated Series/Fabricated.Series.S01E02.mkv"),
                status=TerminalStatus.SUSPICIOUS,
                parse=ParseResult(
                    series_hint="Fabricated Series",
                    season=1,
                    episodes=(2,),
                    year=2024,
                ),
                show=show,
                evidence=evidence,
                operation_group_id="op-fabricated-two",
                reason="conflicting fabricated numbering evidence",
            ),
        ),
    )


def test_review_reference_is_stable_across_unicode_and_case_equivalence() -> None:
    first = stable_review_ref("Example/Épisode.mkv")
    second = stable_review_ref("example/e\u0301PISODE.mkv")

    assert first == second
    assert first.startswith("review-")
    assert len(first) == len("review-") + 16


def test_unresolved_report_contains_stable_review_reference() -> None:
    plan = _review_plan()
    bundle = render_audit_bundle(plan)
    rows = list(csv.DictReader(io.StringIO(bundle.unresolved_csv.decode("utf-8-sig"))))

    assert len(rows) == 2
    assert rows[0]["review_ref"] == stable_review_ref(rows[0]["source"])
    assert rows[1]["review_ref"] == stable_review_ref(rows[1]["source"])


def test_override_stub_groups_review_items_and_remains_valid(tmp_path: Path) -> None:
    manifest = plan_to_manifest(_review_plan())
    rendered = render_override_stub(manifest)
    text = rendered.decode("utf-8")

    assert text.count("[[shows]]") == 1
    assert text.count("review-") == 2
    assert 'key = "Fabricated Series"' in text
    assert "# observed_tvmaze_id = 4242" in text
    assert 'numbering_mode = "aired"' in text
    assert "tvmaze_id = 4242" not in text.replace("# observed_tvmaze_id = 4242", "")

    path = tmp_path / "review-overrides.toml"
    path.write_bytes(rendered)
    catalog = load_overrides(path)
    assert catalog.schema_version == 2
    assert len(catalog.shows) == 1
    assert catalog.shows[0].key == "Fabricated Series"
    assert catalog.shows[0].tvmaze_id is None


def test_override_stub_cli_reads_only_explicit_plan_and_emits_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    import json

    plan_path.write_text(
        json.dumps(plan_to_manifest(_review_plan()), ensure_ascii=False),
        encoding="utf-8",
    )

    assert main(["overrides", "stub", str(plan_path)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("schema_version = 2\n")
    assert "Fabricated Series" in captured.out
    assert str(tmp_path) not in captured.out


def test_override_stub_rejects_invalid_plan_without_echoing_local_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "private-location" / "plan.json"
    plan_path.parent.mkdir()
    plan_path.write_text("{}", encoding="utf-8")

    assert main(["overrides", "stub", str(plan_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Plan manifest invalid:" in captured.err
    assert str(tmp_path) not in captured.err
