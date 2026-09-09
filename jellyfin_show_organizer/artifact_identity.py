"""Path-free build identity shared by packaging and runtime verification.

This is integrity/provenance metadata, not a signature or publisher trust proof.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

IDENTITY_FILE = "_build_identity.json"


def package_hashes(package: Path) -> dict[str, str]:
    paths = sorted(
        path
        for path in package.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".json", ".toml"}
        and path.name != IDENTITY_FILE
    )
    return {
        path.relative_to(package).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }


def read_identity(package: Path) -> dict[str, object] | None:
    try:
        value = json.loads((package / IDENTITY_FILE).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "commit",
            "dirty",
            "files",
        }:
            return None
        if value["schema"] != 1 or not isinstance(value["dirty"], bool):
            return None
        if not isinstance(value["commit"], str) or not re.fullmatch(
            r"[0-9a-f]{40}", value["commit"]
        ):
            return None
        if not value["files"] or value["files"] != package_hashes(package):
            return None
        return value
    except (OSError, ValueError):
        return None


def build_identity(root: Path) -> dict[str, object] | None:
    package = root / "jellyfin_show_organizer"
    # An sdist carries its original identity; never inherit a surrounding repo.
    if (package / IDENTITY_FILE).exists():
        identity = read_identity(package)
        if identity is None:
            raise ValueError("source distribution build identity is invalid")
        return identity
    if not (root / ".git").exists():
        return None

    def git(*args: str) -> str:
        return subprocess.check_output(
            ("git", "-C", str(root), *args), text=True, encoding="utf-8", timeout=10
        ).strip()

    commit = git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid build revision")
    return {
        "schema": 1,
        "commit": commit,
        "dirty": bool(git("status", "--porcelain=v1", "--untracked-files=all")),
        "files": package_hashes(package),
    }
