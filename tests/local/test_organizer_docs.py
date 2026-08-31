from pathlib import Path

import pytest

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"
ARCHITECTURE = ROOT / "docs" / "jellyfin-show-organizer-architecture.md"
RUNBOOK = ROOT / "docs" / "jellyfin-show-organizer-runbook.md"


def test_windows_runbook_uses_venv_python_without_policy_changes():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert r".\.venv\Scripts\python.exe" in text
    assert "Activate.ps1` is not required" in text
    assert "Set-ExecutionPolicy` for this project" in text


def test_runbook_makes_shows_only_boundary_explicit():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "**Shows-only**" in text
    assert "Movies/      <- out of scope; never authorize or inspect" in text
    assert "Do not point it at a Movies directory" in text


def test_runbook_separates_operational_stages_and_has_no_apply_command():
    text = RUNBOOK.read_text(encoding="utf-8")

    for heading in (
        "### 1. Install and configure",
        "### 2. Scan and plan",
        "### 3. Review unresolved and apply local overrides",
        "### 4. Audit and preflight",
        "### 5. Approval",
        "### 6. Apply",
        "### 7. Verification and recovery",
    ):
        assert heading in text

    assert "There is intentionally no organizer `apply` command" in text


def test_operational_plan_is_documented_as_non_mutating_and_auditable():
    readme = README.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for text in (readme, runbook):
        assert "jmo plan" in text
        assert "plan.json" in text
        assert "preflight" in text
        assert "never moves" in text


def test_runbook_documents_current_non_mutating_surfaces():
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "jmo --version" in runbook
    assert "jmo overrides validate local-overrides.toml" in runbook
    assert "does not load local overrides implicitly" in runbook
    assert "Subtitle sidecars are a companion planning layer" in runbook
    assert ".idx` + `.sub` pairs" in runbook
    assert "performs no media writes" in runbook


def test_runbook_documents_current_numbering_and_release_boundaries():
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for mode in (
        "`aired`",
        "`absolute`",
        "`parenthesized-absolute`",
        "`segment-title`",
        "`special`",
        "`date`",
    ):
        assert mode in runbook

    assert "Mixed numbering families" in runbook
    assert "read-only repository permissions" in runbook
    assert "does not contain package-registry publishing credentials" in runbook
    assert "docs/releasing.md" in runbook


def test_docs_keep_privacy_and_data_driven_extension_rules_explicit():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    architecture = ARCHITECTURE.read_text(encoding="utf-8")

    assert "Public repository privacy rules" in runbook
    assert "synthetic paths beneath `tmp_path`" in runbook
    assert "overrides-v1.toml" in runbook
    assert "cache/provider boundary" in runbook
    assert "standalone Python package" in architecture
    assert "There is currently no `apply` command" in architecture
    assert "private library should be reduced" in architecture
