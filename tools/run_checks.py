"""Run the complete local contributor validation sequence.

This uses only the standard library so it can run after the editable
development install. The individual checks remain separate in CI; this script
is a convenience wrapper, not a second test policy.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _commands() -> tuple[tuple[str, ...], ...]:
    python = (sys.executable,)
    return (
        (*python, "tools/check_ci_constraints.py"),
        (
            *python,
            "-m",
            "ruff",
            "check",
            "jellyfin_show_organizer",
            "tests",
            "tools",
            "setup.py",
        ),
        (
            *python,
            "-m",
            "ruff",
            "format",
            "--check",
            "jellyfin_show_organizer",
            "tests",
            "tools",
            "setup.py",
        ),
        (*python, "-m", "mypy", "jellyfin_show_organizer", "tests"),
        (*python, "-m", "pytest"),
        (*python, "tools/check_repository_safety.py"),
    )


def run(commands: Sequence[Sequence[str]] | None = None) -> int:
    """Run checks in CI order and stop at the first failure."""

    for command in _commands() if commands is None else commands:
        print(f"\n>>> {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode:
            print(f"Check failed with exit code {completed.returncode}.")
            return completed.returncode
    print("\nAll local checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
