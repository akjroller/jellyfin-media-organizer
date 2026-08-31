from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CONSTRAINTS = ROOT / "requirements-ci.txt"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+")


def _requirement_name(requirement: str) -> str:
    match = NAME_RE.match(requirement.strip())
    if match is None:
        raise ValueError(f"cannot parse requirement: {requirement!r}")
    return match.group(0).lower().replace("_", "-")


def _constraint_names() -> set[str]:
    names: set[str] = set()
    for raw_line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise SystemExit(f"CI constraint is not exactly pinned: {line}")
        names.add(_requirement_name(line))
    return names


def main() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dev_requirements = project["project"]["optional-dependencies"]["dev"]
    build_requirements = project["build-system"]["requires"]

    required = {
        *(_requirement_name(item) for item in dev_requirements),
        *(_requirement_name(item) for item in build_requirements),
        "build",
    }
    constrained = _constraint_names()
    missing = sorted(required - constrained)
    if missing:
        raise SystemExit(
            "requirements-ci.txt is missing constrained tools: " + ", ".join(missing)
        )

    print("CI constraints cover all direct development and build tools")


if __name__ == "__main__":
    main()
