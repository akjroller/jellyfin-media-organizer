from __future__ import annotations

import json
from pathlib import Path

import pytest

from jellyfin_show_organizer.cli import main
from jellyfin_show_organizer.schema import (
    LEGACY_PLAN_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    validate_manifest,
)

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]


def test_frozen_v2_plan_validates_but_review_requires_regeneration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = json.loads(
        (ROOT / "tests/fixtures/plan-schema-v2-duplicate.json").read_text(
            encoding="utf-8"
        )
    )
    validate_manifest(manifest)
    assert manifest["schema_version"] == LEGACY_PLAN_SCHEMA_VERSION

    plan = tmp_path / "legacy-plan.json"
    plan.write_text(json.dumps(manifest), encoding="utf-8")
    base = tmp_path / "base.toml"
    base.write_text("schema_version = 4\n", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()

    exit_code = main(
        [
            "review",
            str(plan),
            "--overrides",
            str(base),
            "--output",
            str(tmp_path / "active.toml"),
            "--session",
            str(tmp_path / "session.json"),
            "--cache-dir",
            str(cache),
            "--offline",
        ]
    )

    assert exit_code == 2
    error = capsys.readouterr().err
    assert f"review requires plan schema v{PLAN_SCHEMA_VERSION}" in error
    assert "regenerate the plan" in error
    assert "current `jmo plan`" in error
    assert not (tmp_path / "active.toml").exists()
    assert not (tmp_path / "session.json").exists()
