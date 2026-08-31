from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_FILE = ROOT / "pyproject.toml"
REPOSITORY_URL = "https://github.com/akjroller/jellyfin-media-organizer"


def _project_metadata():
    return tomllib.loads(PROJECT_FILE.read_text(encoding="utf-8"))


def test_installable_metadata_uses_standalone_project_identity() -> None:
    metadata = _project_metadata()
    project = metadata["project"]

    assert project["name"] == "jellyfin-media-organizer"
    assert project["dependencies"] == []
    assert project["urls"] == {
        "Homepage": REPOSITORY_URL,
        "Repository": REPOSITORY_URL,
        "Issues": f"{REPOSITORY_URL}/issues",
    }


def test_only_standalone_application_package_is_shipped() -> None:
    metadata = _project_metadata()
    setuptools = metadata["tool"]["setuptools"]
    assert setuptools["packages"] == ["jellyfin_show_organizer"]

    scripts = metadata["project"]["scripts"]
    assert scripts == {
        "jmo": "jellyfin_show_organizer.cli:main",
        "organizer": "jellyfin_show_organizer.cli:main",
    }


def test_project_metadata_does_not_restore_legacy_upstream_identity() -> None:
    text = PROJECT_FILE.read_text(encoding="utf-8").casefold()
    assert "mnamer" not in text
    assert "jkwill87" not in text