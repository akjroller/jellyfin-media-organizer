from __future__ import annotations

from pathlib import Path

import pytest

from tools import check_repository_safety as safety


def _without_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety, "_reachable_commit_messages", lambda: ())


@pytest.mark.parametrize(
    "suffix",
    [".py", ".ps1", ".sh", ".bat", ".cmd", ".ini", ".cfg"],
)
def test_repository_safety_scans_source_script_and_config_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    suffix: str,
) -> None:
    relative = Path(f"sample{suffix}")
    private_path = "C:" + "\\" + "Users" + "\\" + "PrivateProfile" + "\\" + "state"
    (tmp_path / relative).write_text(private_path, encoding="utf-8")
    monkeypatch.setattr(safety, "ROOT", tmp_path)
    monkeypatch.setattr(safety, "_tracked_paths", lambda: (relative,))
    _without_history(monkeypatch)

    with pytest.raises(SystemExit, match="machine-specific user-profile path"):
        safety.main()


def test_repository_safety_scans_source_for_assistant_attribution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    relative = Path("sample.py")
    attribution = "".join(("Chat", "GPT"))
    (tmp_path / relative).write_text(attribution, encoding="utf-8")
    monkeypatch.setattr(safety, "ROOT", tmp_path)
    monkeypatch.setattr(safety, "_tracked_paths", lambda: (relative,))
    _without_history(monkeypatch)

    with pytest.raises(SystemExit, match="development-assistant attribution"):
        safety.main()


def test_repository_safety_keeps_documented_example_profile_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    relative = Path("example.ps1")
    example_path = "C:" + "\\" + "Users" + "\\" + "ExampleUser" + "\\" + "state"
    (tmp_path / relative).write_text(example_path, encoding="utf-8")
    monkeypatch.setattr(safety, "ROOT", tmp_path)
    monkeypatch.setattr(safety, "_tracked_paths", lambda: (relative,))
    _without_history(monkeypatch)

    safety.main()

    assert "repository safety gate passed" in capsys.readouterr().out


def test_repository_safety_scans_reachable_commit_messages_for_profile_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_path = "C:" + "\\" + "Users" + "\\" + "PrivateProfile" + "\\" + "state"
    monkeypatch.setattr(safety, "ROOT", tmp_path)
    monkeypatch.setattr(safety, "_tracked_paths", lambda: ())
    monkeypatch.setattr(safety, "_reachable_commit_messages", lambda: (private_path,))

    with pytest.raises(SystemExit, match="reachable commit message"):
        safety.main()


def test_repository_safety_scans_reachable_commit_messages_for_attribution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attribution = "".join(("Chat", "GPT"))
    monkeypatch.setattr(safety, "ROOT", tmp_path)
    monkeypatch.setattr(safety, "_tracked_paths", lambda: ())
    monkeypatch.setattr(safety, "_reachable_commit_messages", lambda: (attribution,))

    with pytest.raises(SystemExit, match="reachable commit message"):
        safety.main()


def test_repository_safety_allows_clean_reachable_commit_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(safety, "ROOT", tmp_path)
    monkeypatch.setattr(safety, "_tracked_paths", lambda: ())
    monkeypatch.setattr(
        safety,
        "_reachable_commit_messages",
        lambda: ("Harden deterministic audit reporting", "Improve recovery tests"),
    )

    safety.main()

    assert "repository safety gate passed" in capsys.readouterr().out


EXCEPTED_COMMIT = "455bf1bbea0bf11dabdcecd70a068f0d6e178a27"
OTHER_COMMIT = "0" * 40


def _log(*records: tuple[str, str]) -> bytes:
    return b"".join(
        commit.encode("ascii") + b"\x01" + message.encode("utf-8") + b"\0"
        for commit, message in records
    )


def _excepted_line() -> str:
    return safety.KNOWN_COMMIT_MESSAGE_EXCEPTIONS[EXCEPTED_COMMIT]


def test_known_exception_removes_only_its_line_from_only_its_commit() -> None:
    message = f"Subject\n\nBody text\n\n{_excepted_line()}\n\nTrailer: kept\n"

    (cleaned,) = safety._parse_commit_log(_log((EXCEPTED_COMMIT, message)))

    assert safety._privacy_problem(cleaned) is None
    assert "Body text" in cleaned
    assert "Trailer: kept" in cleaned


def test_same_line_in_any_other_commit_still_fails() -> None:
    message = f"Subject\n\n{_excepted_line()}\n"

    (kept,) = safety._parse_commit_log(_log((OTHER_COMMIT, message)))

    assert safety._privacy_problem(kept) == "development-assistant attribution"


def test_known_exception_does_not_hide_other_problems_in_that_commit() -> None:
    attribution = "".join(("Chat", "GPT"))
    private_path = "C:" + "\\" + "Users" + "\\" + "PrivateProfile" + "\\" + "state"

    (with_other_attribution,) = safety._parse_commit_log(
        _log((EXCEPTED_COMMIT, f"{_excepted_line()}\nWritten with {attribution}\n"))
    )
    (with_private_path,) = safety._parse_commit_log(
        _log((EXCEPTED_COMMIT, f"{_excepted_line()}\nSaved in {private_path}\n"))
    )

    assert safety._privacy_problem(with_other_attribution) is not None
    assert safety._privacy_problem(with_private_path) == (
        "machine-specific user-profile path"
    )


def test_parse_commit_log_keeps_ordinary_messages_in_order() -> None:
    raw = _log((OTHER_COMMIT, "First\n"), ("1" * 40, "Second\n")) + b"\n"

    assert safety._parse_commit_log(raw) == ("First\n", "Second\n")
