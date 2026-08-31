from pathlib import Path

import pytest

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]
PROJECT = ROOT / "pyproject.toml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release-artifacts.yml"
RELEASE_DOC = ROOT / "docs" / "releasing.md"


def test_package_version_has_one_source_of_truth():
    project = PROJECT.read_text(encoding="utf-8")
    package_init = (ROOT / "jellyfin_show_organizer" / "__init__.py").read_text(
        encoding="utf-8"
    )

    assert 'dynamic = ["version"]' in project
    assert 'version = { attr = "jellyfin_show_organizer.__version__" }' in project
    assert '__version__ = "0.1.0"' in package_init


def test_project_metadata_documents_supported_python_versions():
    project = PROJECT.read_text(encoding="utf-8")

    assert 'requires-python = ">=3.12"' in project
    for version in ("3.12", "3.13", "3.14"):
        assert f'"Programming Language :: Python :: {version}"' in project


def test_release_workflow_is_deliberate_and_read_only():
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert '      - "v*"' in workflow
    assert "  contents: read" in workflow
    assert "pull_request:" not in workflow
    assert "branches:" not in workflow
    assert "id-token: write" not in workflow
    assert "pypi" not in workflow.casefold()
    assert "publish" not in workflow.casefold()


def test_release_policy_keeps_plan_only_and_private_data_boundaries_explicit():
    text = RELEASE_DOC.read_text(encoding="utf-8")

    assert "Semantic Versioning" in text
    assert "Current releases are **plan-only**" in text
    assert "There is currently no automatic PyPI" in text
    assert "Real media, inventories, reports, provider caches, manifests" in text
