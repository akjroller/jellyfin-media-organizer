from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_SUFFIXES = {
    ".avi",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ts",
    ".webm",
    ".wmv",
}
GENERATED_NAMES = {
    "audit.csv",
    "inventory.csv",
    "organizer-plan.json",
    "plan.json",
}
GENERATED_PARTS = {
    ".jmo-cache",
    ".jmo-local",
    "organizer-cache",
    "organizer-reports",
}
PUBLIC_TEXT_SUFFIXES = {".json", ".md", ".toml", ".txt", ".yaml", ".yml"}
SELF = Path("tools/check_repository_safety.py")

PROFILE_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/](?!ExampleUser[\\/])[^\\/\s\"']+[\\/]"),
    re.compile(r"/home/(?!example/|runner/)[^/\s\"']+/"),
    re.compile(r"/Users/(?!ExampleUser/)[^/\s\"']+/"),
)
ASSISTANT_ATTRIBUTION = re.compile(
    r"(?i)\b(ChatGPT|OpenAI|Claude Code|GitHub Copilot)\b"
)


def _tracked_paths() -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(
        Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item
    )


def _is_synthetic_fixture(path: Path) -> bool:
    parts = path.parts
    return len(parts) >= 2 and parts[0] == "tests" and parts[1] == "fixtures"


def main() -> None:
    problems: list[str] = []

    for relative in _tracked_paths():
        lowered_parts = {part.casefold() for part in relative.parts}
        if relative.suffix.casefold() in MEDIA_SUFFIXES:
            problems.append(f"tracked media file: {relative.as_posix()}")

        if not _is_synthetic_fixture(relative):
            if relative.name.casefold() in GENERATED_NAMES:
                problems.append(
                    f"tracked generated organizer artifact: {relative.as_posix()}"
                )
            if lowered_parts & GENERATED_PARTS:
                problems.append(
                    f"tracked generated organizer directory: {relative.as_posix()}"
                )

        if relative == SELF or relative.suffix.casefold() not in PUBLIC_TEXT_SUFFIXES:
            continue

        try:
            text = (ROOT / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"non-UTF-8 public text file: {relative.as_posix()}")
            continue

        for pattern in PROFILE_PATTERNS:
            if pattern.search(text):
                problems.append(
                    f"machine-specific user-profile path in public text: {relative.as_posix()}"
                )
                break

        if ASSISTANT_ATTRIBUTION.search(text):
            problems.append(
                f"development-assistant attribution in public text: {relative.as_posix()}"
            )

    if problems:
        formatted = "\n".join(f"- {problem}" for problem in sorted(set(problems)))
        raise SystemExit("repository safety gate failed:\n" + formatted)

    print("repository safety gate passed")


if __name__ == "__main__":
    main()
