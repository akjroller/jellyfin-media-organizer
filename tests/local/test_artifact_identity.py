from __future__ import annotations

import json
from pathlib import Path

import pytest

from jellyfin_show_organizer import artifact_identity, run_provenance
from jellyfin_show_organizer.artifact_identity import (
    IDENTITY_FILE,
    build_identity,
    package_hashes,
    read_identity,
)

pytestmark = pytest.mark.local


def _package(tmp_path: Path) -> Path:
    package = tmp_path / "jellyfin_show_organizer"
    package.mkdir()
    (package / "__init__.py").write_text("VERSION = 1\n")
    (package / "data").mkdir()
    (package / "data" / "schema.json").write_text("{}")
    identity = {
        "schema": 1,
        "commit": "a" * 40,
        "dirty": False,
        "files": package_hashes(package),
    }
    (package / IDENTITY_FILE).write_text(json.dumps(identity))
    return package


def test_installed_identity_ignores_surrounding_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    package = _package(tmp_path)
    monkeypatch.setattr(run_provenance, "__file__", str(package / "run_provenance.py"))

    def unexpected(*args):
        raise AssertionError("installed package must not consult Git")

    monkeypatch.setattr(run_provenance, "_run_git", unexpected)
    revision = run_provenance.detect_source_revision()
    assert revision.commit == "a" * 40
    assert revision.dirty is False
    assert build_identity(tmp_path) == read_identity(package)


@pytest.mark.parametrize("mutation", ["changed", "extra", "missing", "invalid"])
def test_modified_package_identity_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
):
    package = _package(tmp_path)
    if mutation == "changed":
        (package / "__init__.py").write_text("VERSION = 2\n")
    elif mutation == "extra":
        (package / "unexpected.py").write_text("pass\n")
    elif mutation == "missing":
        (package / "data" / "schema.json").unlink()
    else:
        (package / IDENTITY_FILE).write_text("[]")
    monkeypatch.setattr(run_provenance, "__file__", str(package / "run_provenance.py"))
    assert run_provenance.detect_source_revision().state == "unavailable"
    with pytest.raises(ValueError, match="identity is invalid"):
        build_identity(tmp_path)


def test_unstamped_install_does_not_inherit_parent_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    package = tmp_path / "venv" / "site-packages" / "jellyfin_show_organizer"
    package.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(run_provenance, "__file__", str(package / "run_provenance.py"))
    assert run_provenance.detect_source_revision().state == "unavailable"
    assert build_identity(package.parent) is None


@pytest.mark.parametrize("dirty", [False, True])
def test_build_records_actual_checkout_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirty: bool
):
    package = _package(tmp_path)
    (package / IDENTITY_FILE).unlink()
    (tmp_path / ".git").mkdir()

    def fake_git(args, **kwargs):
        return "a" * 40 if "rev-parse" in args else (" M changed.py" if dirty else "")

    monkeypatch.setattr(artifact_identity.subprocess, "check_output", fake_git)
    identity = build_identity(tmp_path)
    assert identity is not None
    assert identity["dirty"] is dirty
    assert identity["files"] == package_hashes(package)


def test_bad_build_revision_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    package = _package(tmp_path)
    (package / IDENTITY_FILE).unlink()
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        artifact_identity.subprocess, "check_output", lambda *args, **kwargs: "invalid"
    )
    with pytest.raises(ValueError, match="invalid build revision"):
        build_identity(tmp_path)
