from pathlib import Path

import pytest

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]
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
        "### 1. Scan",
        "### 2. Plan",
        "### 3. Audit",
        "### 4. Approval",
        "### 5. Apply",
        "### 6. Verification",
        "### 7. Recovery",
    ):
        assert heading in text

    assert "There is intentionally no organizer `apply` command" in text


def test_docs_keep_privacy_and_data_driven_extension_rules_explicit():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    architecture = ARCHITECTURE.read_text(encoding="utf-8")

    assert "Public repository privacy rules" in runbook
    assert "synthetic paths beneath `tmp_path`" in runbook
    assert "overrides-v1.toml" in runbook
    assert "persistent cache layer" in runbook
    assert "standalone Python package" in architecture
    assert "There is currently no `apply` command" in architecture
    assert "private library should be reduced" in architecture
