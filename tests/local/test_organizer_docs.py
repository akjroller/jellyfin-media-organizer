from pathlib import Path

import pytest

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"
ARCHITECTURE = ROOT / "docs" / "jellyfin-show-organizer-architecture.md"
RUNBOOK = ROOT / "docs" / "jellyfin-show-organizer-runbook.md"


def test_readme_documents_source_install_without_claiming_registry_release():
    text = README.read_text(encoding="utf-8")

    assert "## Install from source" in text
    assert "JMO is not documented as a package-registry install yet" in text
    assert "git clone https://github.com/akjroller/jellyfin-media-organizer.git" in text
    assert "python3 -m venv .venv" in text
    assert r".\.venv\Scripts\python.exe -m pip install ." in text


def test_windows_runbook_uses_venv_python_without_policy_changes():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert r".\.venv\Scripts\python.exe" in text
    assert "`Activate.ps1` is not required" in text
    assert "`Set-ExecutionPolicy` for this project" in text


def test_runbook_has_posix_and_windows_setup_paths():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "### POSIX setup" in text
    assert "./.venv/bin/python -m pip install ." in text
    assert "### Windows setup without PowerShell activation" in text
    assert r".\.venv\Scripts\python.exe -m pip install ." in text


def test_runbook_makes_shows_only_boundary_explicit():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    assert "**Shows-only**" in runbook
    assert "Movies/      <- out of scope; never authorize or inspect" in runbook
    assert "The active scope is **Shows-only**" in readme
    assert "Movies directory, mixed media root" in readme


def test_docs_do_not_pretend_plan_root_or_apply_are_implemented():
    readme = README.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "does not yet accept or scan a media root" in readme
    assert "does **not** yet accept a Shows root" in runbook
    assert "There is intentionally no organizer `apply` command" in runbook
    assert "jmo plan <" not in readme
    assert "organizer plan <" not in runbook


def test_runbook_separates_full_product_lifecycle():
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


def test_docs_keep_privacy_and_machine_neutrality_explicit():
    readme = README.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")
    architecture = ARCHITECTURE.read_text(encoding="utf-8")

    assert "Public repository privacy rules" in runbook
    assert "synthetic paths beneath `tmp_path`" in runbook
    assert "one contributor's filesystem layout" in readme
    assert "Do not encode a contributor drive letter" in runbook
    assert "overrides-v1.toml" in runbook
    assert "provider-adapter boundary" in runbook
    assert "standalone Python package" in architecture
    assert "There is currently no `apply` command" in architecture
    assert "private library should be reduced" in architecture


def test_runbook_documents_cache_contract_without_inventing_cli_flags():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "## Provider cache and offline policy" in text
    assert "hard zero-provider-call contract" in text
    assert (
        "Do not document `--offline` or refresh examples as usable CLI syntax" in text
    )
    assert "jmo plan --offline" not in text


def test_documented_development_commands_match_ci_gates():
    readme = README.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for command in (
        "python -m ruff check jellyfin_show_organizer tests tools",
        "python -m ruff format --check jellyfin_show_organizer tests tools",
        "python -m mypy jellyfin_show_organizer tests",
        "python -m pytest",
        "python tools/check_ci_constraints.py",
        "python tools/check_repository_safety.py",
    ):
        assert command in readme
        assert command in runbook
