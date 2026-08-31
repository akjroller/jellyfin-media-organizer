from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_FILE = ROOT / "pyproject.toml"
REPOSITORY_URL = "https://github.com/akjroller/jellyfin-media-organizer"


def _project_text() -> str:
    return PROJECT_FILE.read_text(encoding="utf-8")


def test_installable_metadata_uses_standalone_project_identity() -> None:
    text = _project_text()

    assert 'name = "jellyfin-media-organizer"' in text
    assert 'dependencies = []' in text
    assert f'Homepage = "{REPOSITORY_URL}"' in text
    assert f'Repository = "{REPOSITORY_URL}"' in text
    assert f'Issues = "{REPOSITORY_URL}/issues"' in text


def test_only_standalone_application_package_is_shipped() -> None:
    text = _project_text()

    assert 'packages = ["jellyfin_show_organizer"]' in text
    assert 'jmo = "jellyfin_show_organizer.cli:main"' in text
    assert 'organizer = "jellyfin_show_organizer.cli:main"' in text


def test_project_metadata_does_not_restore_legacy_upstream_identity() -> None:
    text = _project_text().casefold()
    assert "mnamer" not in text
    assert "jkwill87" not in text
