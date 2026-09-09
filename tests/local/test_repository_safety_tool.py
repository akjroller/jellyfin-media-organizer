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
