from __future__ import annotations

import json
from pathlib import Path

import pytest

from jellyfin_show_organizer import run_provenance
from jellyfin_show_organizer.decision_hash import stable_decision_hash
from jellyfin_show_organizer.models import (
    CacheSnapshot,
    OrganizerPlan,
    PlanProvenance,
    PlanRecord,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.reports import render_audit_bundle
from jellyfin_show_organizer.run_provenance import (
    SourceRevision,
    build_run_provenance,
    detect_source_revision,
    render_run_provenance,
)
from jellyfin_show_organizer.schema import stable_plan_hash

pytestmark = pytest.mark.local

_COMMIT = "a" * 40
_CONFIG = "b" * 64
_OVERRIDES = "c" * 64


def _plan() -> OrganizerPlan:
    source = SourceFile(
        relative_path="Example Series/Unknown.mkv",
        extension=".mkv",
        fingerprint=SourceFingerprint(size=100, mtime_ns=200),
    )
    return OrganizerPlan(
        schema_version=1,
        overrides_version=1,
        records=(PlanRecord(source=source, status=TerminalStatus.UNRESOLVED),),
        provenance=PlanProvenance(
            tool_version="0.1.0",
            config_snapshot_id=_CONFIG,
            overrides_snapshot_id=_OVERRIDES,
            cache_snapshots=(
                CacheSnapshot(
                    provider="tvmaze",
                    kind="search",
                    request_key="search:example-series",
                    snapshot_id="d" * 64,
                    state="ok",
                ),
                CacheSnapshot(
                    provider="tvmaze",
                    kind="episodes",
                    request_key="episodes:123",
                    snapshot_id="e" * 64,
                    state="stale",
                ),
            ),
        ),
    )


def _build(source_revision: SourceRevision) -> run_provenance.RunProvenance:
    return build_run_provenance(
        _plan(),
        source_revision=source_revision,
        provider_mode="offline",
        provider_failure=True,
        max_path_length=240,
        max_component_length=180,
        overrides_configured=True,
        preflight_ready=False,
        preflight_finding_count=2,
    )


def test_detect_source_revision_records_clean_git_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_git(_root: Path, *args: str) -> tuple[int, str]:
        if args == ("rev-parse", "--is-inside-work-tree"):
            return 0, "true\n"
        if args == ("rev-parse", "HEAD"):
            return 0, f"{_COMMIT}\n"
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return 0, ""
        raise AssertionError(args)

    monkeypatch.setattr(run_provenance, "_run_git", fake_git)

    assert detect_source_revision(Path("synthetic-checkout")) == SourceRevision(
        state="git",
        commit=_COMMIT,
        dirty=False,
    )


def test_detect_source_revision_collapses_dirty_details_to_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_git(_root: Path, *args: str) -> tuple[int, str]:
        if args == ("rev-parse", "--is-inside-work-tree"):
            return 0, "true\n"
        if args == ("rev-parse", "HEAD"):
            return 0, f"{_COMMIT}\n"
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return 0, " M private-name.txt\n?? local-only.txt\n"
        raise AssertionError(args)

    monkeypatch.setattr(run_provenance, "_run_git", fake_git)
    revision = detect_source_revision(Path("synthetic-checkout"))
    rendered = json.dumps(
        _build(revision).to_dict(), sort_keys=True, separators=(",", ":")
    )

    assert revision.dirty is True
    assert "private-name.txt" not in rendered
    assert "local-only.txt" not in rendered


def test_detect_source_revision_falls_back_when_git_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_provenance, "_run_git", lambda _root, *_args: None)

    assert detect_source_revision(Path("synthetic-install")) == SourceRevision(
        state="unavailable",
        commit=None,
        dirty=None,
    )


def test_rendered_provenance_is_canonical_path_free_and_hash_linked() -> None:
    plan = _plan()
    provenance = build_run_provenance(
        plan,
        source_revision=SourceRevision(state="git", commit=_COMMIT, dirty=False),
        provider_mode="offline",
        provider_failure=True,
        max_path_length=240,
        max_component_length=180,
        overrides_configured=True,
        preflight_ready=False,
        preflight_finding_count=2,
    )

    rendered = render_run_provenance(provenance)
    payload = json.loads(rendered)

    assert rendered.endswith(b"\n")
    assert rendered == render_run_provenance(provenance)
    assert payload["plan_sha256"] == stable_plan_hash(plan)
    assert payload["decision_sha256"] == stable_decision_hash(plan)
    assert payload["config_snapshot_id"] == _CONFIG
    assert payload["overrides_snapshot_id"] == _OVERRIDES
    assert payload["provider"] == {
        "name": "tvmaze",
        "mode": "offline",
        "failure": True,
        "cache_snapshot_count": 2,
        "cache_states": {"ok": 1, "stale": 1},
        "cache_kinds": {"episodes": 1, "search": 1},
    }
    assert payload["records"]["total"] == 1
    assert payload["records"]["statuses"]["unresolved"] == 1
    assert payload["companions"]["total"] == 0
    assert payload["preflight"] == {"ready": False, "finding_count": 2}
    text = rendered.decode("utf-8")
    assert "Example Series" not in text
    assert "synthetic-checkout" not in text
    assert "shows_root" not in text
    assert "destination_root" not in text
    assert "output_dir" not in text
    assert "cache_dir" not in text
    assert "overrides_path" not in text


def test_run_provenance_does_not_change_plan_or_decision_hashes() -> None:
    plan = _plan()
    plan_hash = stable_plan_hash(plan)
    decision_hash = stable_decision_hash(plan)

    clean = render_run_provenance(
        build_run_provenance(
            plan,
            source_revision=SourceRevision(state="git", commit=_COMMIT, dirty=False),
            provider_mode="online",
            provider_failure=False,
            max_path_length=240,
            max_component_length=180,
            overrides_configured=False,
            preflight_ready=True,
            preflight_finding_count=0,
        )
    )
    dirty = render_run_provenance(
        build_run_provenance(
            plan,
            source_revision=SourceRevision(state="git", commit=_COMMIT, dirty=True),
            provider_mode="online",
            provider_failure=False,
            max_path_length=240,
            max_component_length=180,
            overrides_configured=False,
            preflight_ready=True,
            preflight_finding_count=0,
        )
    )

    assert clean != dirty
    assert stable_plan_hash(plan) == plan_hash
    assert stable_decision_hash(plan) == decision_hash


def test_audit_bundle_places_run_provenance_immediately_before_plan_json() -> None:
    rendered = render_run_provenance(_build(SourceRevision("unavailable", None, None)))
    bundle = render_audit_bundle(_plan(), run_provenance_json=rendered)
    names = [name for name, _data in bundle.files()]

    assert names[-2:] == ["run-provenance.json", "plan.json"]
    assert bundle.run_provenance_json == rendered
