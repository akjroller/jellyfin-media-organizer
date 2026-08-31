from pathlib import Path

import pytest

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"
ARCHITECTURE = ROOT / "docs" / "jellyfin-show-organizer-architecture.md"
RUNBOOK = ROOT / "docs" / "jellyfin-show-organizer-runbook.md"
CONTRIBUTING = ROOT / "docs" / "contributing.md"
TROUBLESHOOTING = ROOT / "docs" / "troubleshooting.md"
RELEASING = ROOT / "docs" / "releasing.md"


def test_windows_runbook_uses_venv_python_without_policy_changes():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert r".\.venv\Scripts\python.exe" in text
    assert "Activate.ps1` is not required" in text
    assert "Set-ExecutionPolicy` for this project" in text


def test_readme_install_is_copyable_without_powershell_activation():
    text = README.read_text(encoding="utf-8")

    assert "git clone https://github.com/akjroller/jellyfin-media-organizer.git" in text
    assert r".\.venv\Scripts\python.exe -m pip install ." in text
    assert r".\.venv\Scripts\jmo.exe plan --help" in text
    assert "execution-policy change" in text


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


def test_contributor_guide_matches_current_extension_boundaries():
    text = CONTRIBUTING.read_text(encoding="utf-8")

    assert "Synthetic fixtures" in text
    assert "ProviderIdentity" in text
    assert "MetadataProvider" in text
    assert "Adding a second provider should require an adapter" in text
    assert "There is currently no `apply` command" in text
    assert "python -m mypy jellyfin_show_organizer tests" in text
    assert r".\.venv\Scripts\python.exe -m pytest" in text


def test_troubleshooting_uses_fabricated_examples_and_never_weakens_safety():
    text = TROUBLESHOOTING.read_text(encoding="utf-8")

    assert "All examples below are fabricated" in text
    assert "ExampleMedia/" in text
    assert "jmo overrides validate LocalState/example-overrides.toml" in text
    assert "An override must not be used to bypass a whole-plan preflight block" in text
    assert "Do not manually move one file" in text
    assert "translate it into a synthetic regression before posting it" in text


def test_readme_links_current_operational_documentation():
    text = README.read_text(encoding="utf-8")

    for path in (
        "docs/jellyfin-show-organizer-runbook.md",
        "docs/troubleshooting.md",
        "docs/contributing.md",
        "docs/local-overrides.md",
        "docs/provider-cache-policy.md",
        "docs/numbering-policies.md",
        "docs/metadata-provider-boundary.md",
        "docs/releasing.md",
    ):
        assert path in text


def test_release_docs_do_not_claim_a_jmo_release_already_exists():
    readme = README.read_text(encoding="utf-8")
    releasing = RELEASING.read_text(encoding="utf-8")

    assert "No JMO release or tag has been created yet by design" in readme
    assert "No JMO release or version tag has been created yet by design" in releasing
    assert "Current releases are **plan-only**" not in readme
    assert "Current releases are **plan-only**" not in releasing
    assert "workflow artifact is not itself a decision to publish" in releasing
