from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION_USE = re.compile(r"^\s*uses:\s*([^\s#]+)(?:\s+#.*)?$", re.MULTILINE)
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _workflow_texts() -> tuple[tuple[str, str], ...]:
    return tuple(
        (path.name, path.read_text(encoding="utf-8"))
        for path in sorted(WORKFLOWS.glob("*.yml"))
    )


def test_every_external_action_is_immutably_pinned() -> None:
    actions = [
        (name, action)
        for name, text in _workflow_texts()
        for action in ACTION_USE.findall(text)
    ]

    assert actions
    assert not [
        f"{name}: {action}"
        for name, action in actions
        if not PINNED_ACTION.fullmatch(action)
    ]


def test_ci_exercises_every_advertised_python_minor() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    for version in ("3.12", "3.13", "3.14"):
        assert f'python-version: "{version}"' in ci


def test_artifact_smoke_uses_the_active_plan_schema_contract() -> None:
    for name in ("ci.yml", "release-artifacts.yml"):
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert workflow.count("from jellyfin_show_organizer.schema import (") == 2
        assert workflow.count("PLAN_SCHEMA_RESOURCE.removeprefix") == 2
        assert workflow.count("load_plan_schema()") == 2
        assert workflow.count("PLAN_SCHEMA_VERSION") == 4
        assert "plan-schema-v1.json" not in workflow
