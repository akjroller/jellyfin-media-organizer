"""Every way empty-folder cleanup can be misused fails safely and clearly."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from jellyfin_show_organizer import prune_execution
from jellyfin_show_organizer.entrypoint import main
from jellyfin_show_organizer.prune_cli import (
    PRUNE_FAILED_EXIT,
    PRUNE_RESTORE_FAILED_EXIT,
)
from jellyfin_show_organizer.prune_execution import (
    PruneError,
    execute_prune,
    plan_prune,
    restore_pruned_dirs,
)

pytestmark = pytest.mark.local

PLAN = "a" * 64


def _apply_journal(path: Path, *sources: str, completed: bool = True) -> Path:
    entries: list[dict[str, object]] = [{"event": "run-started"}]
    entries += [
        {"event": "member-completed", "source_relative_path": source}
        for source in sources
    ]
    if completed:
        entries.append({"event": "run-completed"})
    path.write_text(
        "".join(
            json.dumps(
                {"schema_version": 1, "sequence": index, "plan_sha256": PLAN, **entry}
            )
            + "\n"
            for index, entry in enumerate(entries, 1)
        ),
        encoding="utf-8",
    )
    return path


def _library(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "Shows"
    (source / "Show" / "Season 1").mkdir(parents=True)
    journal = _apply_journal(tmp_path / "apply.jsonl", "Show/Season 1/episode.mkv")
    return source, journal


def _write_lines(path: Path, *lines: object) -> Path:
    path.write_text(
        "".join(
            (line if isinstance(line, str) else json.dumps(line)) + "\n"
            for line in lines
        ),
        encoding="utf-8",
    )
    return path


BAD_APPLY_JOURNALS: dict[str, Callable[[Path], Path]] = {
    "invalid json": lambda p: _write_lines(p, "{not json"),
    "entry not an object": lambda p: _write_lines(p, [1, 2]),
    "unsupported schema": lambda p: _write_lines(
        p, {"schema_version": 9, "sequence": 1, "plan_sha256": PLAN}
    ),
    "sequence gap": lambda p: _write_lines(
        p, {"schema_version": 1, "sequence": 2, "plan_sha256": PLAN}
    ),
    "mixed plans": lambda p: _write_lines(
        p,
        {"schema_version": 1, "sequence": 1, "plan_sha256": PLAN},
        {"schema_version": 1, "sequence": 2, "plan_sha256": "b" * 64},
    ),
    "unfinished run": lambda p: _apply_journal(p, "Show/x.mkv", completed=False),
    "unsafe source path": lambda p: _apply_journal(p, "../outside/x.mkv"),
    "missing file": lambda p: p / "absent.jsonl",
}


@pytest.mark.parametrize("case", sorted(BAD_APPLY_JOURNALS))
def test_bad_apply_journals_are_refused(tmp_path: Path, case: str) -> None:
    source = tmp_path / "Shows"
    source.mkdir()
    journal = BAD_APPLY_JOURNALS[case](tmp_path / "apply.jsonl")

    with pytest.raises(PruneError):
        plan_prune(source, journal)


def test_bad_source_roots_are_refused(tmp_path: Path) -> None:
    journal = _apply_journal(tmp_path / "apply.jsonl", "Show/x.mkv")
    a_file = tmp_path / "file.txt"
    a_file.write_text("x", encoding="utf-8")

    with pytest.raises(PruneError, match="does not exist"):
        plan_prune(tmp_path / "missing", journal)
    with pytest.raises(PruneError, match="must be a directory"):
        plan_prune(a_file, journal)


def test_a_file_in_the_folder_chain_stops_planning(tmp_path: Path) -> None:
    source = tmp_path / "Shows"
    source.mkdir()
    (source / "Show").write_text("not a folder", encoding="utf-8")
    journal = _apply_journal(tmp_path / "apply.jsonl", "Show/Season 1/x.mkv")

    with pytest.raises(PruneError, match="not a plain directory"):
        plan_prune(source, journal)


def test_rollback_completed_moves_are_not_candidates(tmp_path: Path) -> None:
    source = tmp_path / "Shows"
    (source / "Show" / "Season 1").mkdir(parents=True)
    journal = _write_lines(
        tmp_path / "apply.jsonl",
        *[
            {"schema_version": 1, "sequence": index, "plan_sha256": PLAN, **entry}
            for index, entry in enumerate(
                [
                    {
                        "event": "member-completed",
                        "source_relative_path": "Show/Season 1/x.mkv",
                    },
                    {
                        "event": "member-rollback-completed",
                        "source_relative_path": "Show/Season 1/x.mkv",
                    },
                    {"event": "run-completed"},
                ],
                1,
            )
        ],
    )

    assert plan_prune(source, journal).directories == ()


def test_a_failed_rmdir_is_journaled_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, journal = _library(tmp_path)
    plan = plan_prune(source, journal)
    prune_journal = tmp_path / "prune.jsonl"

    def refuse(_path: object) -> None:
        raise OSError(39, "Directory not empty")

    monkeypatch.setattr(prune_execution.os, "rmdir", refuse)

    with pytest.raises(PruneError, match="stopped at Show/Season 1"):
        execute_prune(plan, journal, confirm=plan.token, journal_path=prune_journal)
    events = [
        json.loads(line)["event"] for line in prune_journal.read_text().splitlines()
    ]
    assert events == ["prune-started", "prune-stopped"]


def test_journal_parent_must_exist_and_journal_must_be_new(tmp_path: Path) -> None:
    source, journal = _library(tmp_path)
    plan = plan_prune(source, journal)

    with pytest.raises(PruneError, match="parent does not exist"):
        execute_prune(
            plan, journal, confirm=plan.token, journal_path=tmp_path / "no" / "p.jsonl"
        )
    existing = tmp_path / "existing.jsonl"
    existing.write_text("", encoding="utf-8")
    with pytest.raises(PruneError, match="already exists"):
        execute_prune(plan, journal, confirm=plan.token, journal_path=existing)
    assert (source / "Show" / "Season 1").is_dir()


BAD_PRUNE_JOURNALS: dict[str, Callable[[Path], Path]] = {
    "empty": lambda p: _write_lines(p),
    "invalid json": lambda p: _write_lines(p, "{nope"),
    "entry not an object": lambda p: _write_lines(p, "[]"),
    "unsupported schema": lambda p: _write_lines(
        p, {"schema_version": 7, "sequence": 1}
    ),
    "sequence gap": lambda p: _write_lines(p, {"schema_version": 1, "sequence": 3}),
    "directory not a string": lambda p: _write_lines(
        p,
        {
            "schema_version": 1,
            "sequence": 1,
            "event": "directory-removed",
            "directory": 4,
        },
    ),
    "unsafe directory": lambda p: _write_lines(
        p,
        {
            "schema_version": 1,
            "sequence": 1,
            "event": "directory-removed",
            "directory": "../escape",
        },
    ),
    "missing file": lambda p: p / "absent.jsonl",
}


@pytest.mark.parametrize("case", sorted(BAD_PRUNE_JOURNALS))
def test_bad_prune_journals_are_refused_by_restore(tmp_path: Path, case: str) -> None:
    source = tmp_path / "Shows"
    source.mkdir()
    prune_journal = BAD_PRUNE_JOURNALS[case](tmp_path / "prune.jsonl")

    with pytest.raises(PruneError):
        restore_pruned_dirs(source, prune_journal)


def test_restore_refuses_a_file_where_a_folder_belongs(tmp_path: Path) -> None:
    source = tmp_path / "Shows"
    source.mkdir()
    (source / "Show").write_text("in the way", encoding="utf-8")
    prune_journal = _write_lines(
        tmp_path / "prune.jsonl",
        {
            "schema_version": 1,
            "sequence": 1,
            "event": "directory-removed",
            "directory": "Show",
        },
    )

    with pytest.raises(PruneError, match="non-folder is in the way"):
        restore_pruned_dirs(source, prune_journal)


def test_cli_human_check_lists_folders_and_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, journal = _library(tmp_path)
    args = ["--source-root", str(source), "--apply-journal", str(journal)]

    assert main(["prune-empty-dirs", *args, "--check-only"]) == 0
    out = capsys.readouterr().out
    assert "Empty source folders that would be removed: 2" in out
    assert "  Show/Season 1" in out
    assert "Confirmation token:\nPRUNE:" in out


def test_cli_reports_nothing_to_do(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, journal = _library(tmp_path)
    (source / "Show" / "Season 1" / "keep.txt").write_text("x", encoding="utf-8")

    assert (
        main(
            [
                "prune-empty-dirs",
                "--source-root",
                str(source),
                "--apply-journal",
                str(journal),
            ]
        )
        == 0
    )
    assert "No empty source folders to remove." in capsys.readouterr().out


def test_cli_requires_a_journal_and_a_terminal_or_token(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, journal = _library(tmp_path)
    args = [
        "prune-empty-dirs",
        "--source-root",
        str(source),
        "--apply-journal",
        str(journal),
    ]

    assert main(args) == PRUNE_FAILED_EXIT
    assert "requires --journal" in capsys.readouterr().err

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main([*args, "--journal", str(tmp_path / "p.jsonl")]) == PRUNE_FAILED_EXIT
    assert "requires --confirm-prune" in capsys.readouterr().err
    assert (source / "Show").is_dir()


def test_cli_interactive_prompt_accepts_the_exact_token(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, journal = _library(tmp_path)
    token = plan_prune(source, journal).token
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: token)

    exit_code = main(
        [
            "prune-empty-dirs",
            "--source-root",
            str(source),
            "--apply-journal",
            str(journal),
            "--journal",
            str(tmp_path / "prune.jsonl"),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["removed"] == ["Show/Season 1", "Show"]
    assert not os.path.lexists(source / "Show")


def test_cli_restore_failure_and_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "Shows"
    source.mkdir()
    restore = ["restore-pruned-dirs", "--source-root", str(source), "--prune-journal"]

    assert main([*restore, str(tmp_path / "absent.jsonl")]) == PRUNE_RESTORE_FAILED_EXIT
    assert "Restore failed safely" in capsys.readouterr().err

    journal = _write_lines(
        tmp_path / "prune.jsonl",
        {
            "schema_version": 1,
            "sequence": 1,
            "event": "directory-removed",
            "directory": "Show",
        },
    )
    assert main([*restore, str(journal), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "restored": ["Show"],
        "schema_version": 1,
    }
