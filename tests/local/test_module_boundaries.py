"""Keep the package dependency direction explicit for future contributors."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.local

PACKAGE_ROOT = Path(__file__).parents[2] / "jellyfin_show_organizer"
COMMAND_ADAPTERS = {
    "cli",
    "entrypoint",
    "apply_scope_cli",
    "quarantine_cli",
    "__main__",
}


def _local_dependencies(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            dependencies.add(node.module.split(".", 1)[0])
    return dependencies


def test_core_modules_do_not_depend_on_command_adapters() -> None:
    violations = {
        f"{path.stem} -> {dependency}"
        for path in PACKAGE_ROOT.glob("*.py")
        if path.stem not in COMMAND_ADAPTERS
        for dependency in _local_dependencies(path) & COMMAND_ADAPTERS
    }

    assert not violations, "core-to-command dependency violations: " + ", ".join(
        sorted(violations)
    )
