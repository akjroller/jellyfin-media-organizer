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
PUBLIC_TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cmd",
    ".csv",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SELF = Path("tools/check_repository_safety.py")

PROFILE_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/](?!ExampleUser[\\/])[^\\/\s\"']+[\\/]"),
    re.compile(r"/home/(?!example/|runner/)[^/\s\"']+/"),
    re.compile(r"/Users/(?!ExampleUser/)[^/\s\"']+/"),
)
ASSISTANT_ATTRIBUTION = re.compile(
    r"(?i)\b(ChatGPT|OpenAI|Claude Code|GitHub Copilot)\b"
)
# Published commits that cannot be reworded without rewriting protected `main`.
# Each entry exempts exactly one known line in exactly one commit; every other
# line of that message, and the same text in any other commit, is still checked.
KNOWN_COMMIT_MESSAGE_EXCEPTIONS = {
    # #304 squash merge: the PR description's assistant attribution line became
    # part of the commit message. Kept rather than force-pushing `main`.
    "455bf1bbea0bf11dabdcecd70a068f0d6e178a27": (
        "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)"
    ),
}


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


def _without_known_exception(commit: str, message: str) -> str:
    excepted_line = KNOWN_COMMIT_MESSAGE_EXCEPTIONS.get(commit)
    if excepted_line is None:
        return message
    return "\n".join(
        line for line in message.split("\n") if line.strip() != excepted_line
    )


def _parse_commit_log(raw: bytes) -> tuple[str, ...]:
    """Parse ``%H%x01%B%x00`` records, applying the single-line exceptions."""

    messages: list[str] = []
    for record in raw.split(b"\0"):
        if not record.strip():
            continue
        commit, _, body = record.partition(b"\x01")
        message = _without_known_exception(
            commit.decode("ascii", errors="replace").strip(),
            body.decode("utf-8", errors="replace"),
        )
        if message.strip():
            messages.append(message)
    return tuple(messages)


def _reachable_commit_messages() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "log", "--format=%H%x01%B%x00"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return _parse_commit_log(result.stdout)


def _is_synthetic_fixture(path: Path) -> bool:
    parts = path.parts
    return len(parts) >= 2 and parts[0] == "tests" and parts[1] == "fixtures"


def _privacy_problem(text: str) -> str | None:
    if any(pattern.search(text) for pattern in PROFILE_PATTERNS):
        return "machine-specific user-profile path"
    if ASSISTANT_ATTRIBUTION.search(text):
        return "development-assistant attribution"
    return None


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

        privacy_problem = _privacy_problem(text)
        if privacy_problem is not None:
            problems.append(f"{privacy_problem} in public text: {relative.as_posix()}")

    for index, message in enumerate(_reachable_commit_messages(), 1):
        privacy_problem = _privacy_problem(message)
        if privacy_problem is not None:
            problems.append(
                f"{privacy_problem} in reachable commit message #{index} from HEAD"
            )

    if problems:
        formatted = "\n".join(f"- {problem}" for problem in sorted(set(problems)))
        raise SystemExit("repository safety gate failed:\n" + formatted)

    print("repository safety gate passed")


if __name__ == "__main__":
    main()
