from pathlib import Path

import pytest

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]
RUNBOOK = ROOT / "docs" / "jellyfin-show-organizer-runbook.md"


def test_windows_runbook_uses_venv_python_without_activation():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert r".\.venv\Scripts\python.exe" in text
    assert "Activate.ps1" in text
    assert "Activate.ps1` is\nnot required" in text
    assert "Set-ExecutionPolicy" not in text


def test_runbook_makes_shows_only_boundary_and_movies_prohibition_explicit():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "**Shows-only**" in text
    assert "Movies/      <- out of scope; never authorize or inspect" in text
    assert "Do not point it at a Movies directory" in text


def test_runbook_separates_all_operational_stages():
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

    assert "There is intentionally no organizer\n`apply` command" in text


def test_runbook_documents_privacy_and_data_driven_extension_points():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "Public repository privacy rules" in text
    assert "synthetic paths beneath `tmp_path`" in text
    assert "overrides-v1.toml" in text
    assert "checked-in catalog must\nremain synthetic and generic" in text
    assert "local, untracked override data" in text
    assert 'Do not add `if show == "..."` branches' in text
    assert "provider responses should come through the persistent cache layer" in text
