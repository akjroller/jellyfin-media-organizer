import runpy
import sys
import tomllib
from pathlib import Path

import pytest

from jellyfin_show_organizer import __version__, cli
from jellyfin_show_organizer.apply_contract import ApplyContract
from jellyfin_show_organizer.apply_execution import (
    ApplyExecutionError,
    ApplyExecutionResult,
    PreparedApply,
)
from jellyfin_show_organizer.cli import _planning_config, build_parser, main
from jellyfin_show_organizer.run_provenance import SourceRevision

pytestmark = pytest.mark.local
ROOT = Path(__file__).parents[2]


def test_organizer_version(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"Jellyfin Media Organizer {__version__}"


def test_python_module_entrypoint_reports_version(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setattr(sys, "argv", ["jmo", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("jellyfin_show_organizer", run_name="__main__")

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"Jellyfin Media Organizer {__version__}"


def test_organizer_plan_help(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc_info:
        main(["plan", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    normalized_output = " ".join(output.split())
    assert "usage: organizer plan" in output
    assert "non-mutating audit bundle" in normalized_output
    assert "--destination-root" in output
    assert "--output-dir" in output
    assert "--cache-dir" in output
    assert "--offline" in output


def test_organizer_apply_help_exposes_exact_safety_inputs(
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit) as exc_info:
        main(["apply", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--approve-plan-sha256" in output
    assert "--approve-review-session-sha256" in output
    assert "--approve-source-revision" in output
    assert "--check-only" in output
    assert "--resume" in output


def test_organizer_plan_requires_explicit_paths_without_creating_output(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["plan"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert exc_info.value.code == 2
    assert "required" in captured.err
    assert list(tmp_path.iterdir()) == []


def test_jmo_and_organizer_commands_share_the_same_entrypoint():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]

    assert scripts["jmo"] == "jellyfin_show_organizer.cli:main"
    assert scripts["organizer"] == scripts["jmo"]


def test_plan_config_paths_are_relative_to_config_and_cli_wins(tmp_path: Path):
    config_dir = tmp_path / "configuration"
    config_dir.mkdir()
    config_path = config_dir / "planning.toml"
    config_path.write_text(
        """schema_version = 1

[plan]
destination_root = "../Organized"
output_dir = "../audit-from-config"
cache_dir = "../cache"
provider_mode = "offline"
max_path_length = 220
max_component_length = 170
""",
        encoding="utf-8",
    )
    cli_output = tmp_path / "audit-from-cli"
    args = build_parser().parse_args(
        [
            "plan",
            str(tmp_path / "Shows"),
            "--config",
            str(config_path),
            "--output-dir",
            str(cli_output),
            "--online",
        ]
    )

    config = _planning_config(args)

    assert config.destination_root == config_dir / "../Organized"
    assert config.output_dir == cli_output
    assert config.cache_dir == config_dir / "../cache"
    assert not config.offline
    assert not config.refresh
    assert config.max_path_length == 220
    assert config.max_component_length == 170


def test_plan_config_rejects_unknown_fields(tmp_path: Path):
    config_path = tmp_path / "planning.toml"
    config_path.write_text(
        """schema_version = 1

[plan]
destination_root = "Organized"
output_dir = "audit"
cache_dir = "cache"
unexpected = true
""",
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        ["plan", str(tmp_path / "Shows"), "--config", str(config_path)]
    )

    with pytest.raises(ValueError, match="unknown fields"):
        _planning_config(args)


def _apply_paths(tmp_path: Path) -> tuple[list[str], PreparedApply]:
    plan = tmp_path / "plan.json"
    preflight = tmp_path / "preflight.json"
    provenance = tmp_path / "run-provenance.json"
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    for path in (plan, preflight, provenance):
        path.write_text("{}", encoding="utf-8")
    source.mkdir()
    destination.mkdir()
    prepared = PreparedApply(
        contract=ApplyContract(plan_sha256="a" * 64, groups=()),
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
    )
    args = [
        "apply",
        str(plan),
        "--preflight",
        str(preflight),
        "--run-provenance",
        str(provenance),
        "--source-root",
        str(source),
        "--destination-root",
        str(destination),
        "--approve-plan-sha256",
        "a" * 64,
        "--approve-review-session-sha256",
        "b" * 64,
        "--approve-source-revision",
        "c" * 40,
    ]
    return args, prepared


def _apply_result(*, check_only: bool, journal: Path | None = None):
    return ApplyExecutionResult(
        plan_sha256="a" * 64,
        review_session_sha256="b" * 64,
        groups_total=0,
        groups_completed=0,
        members_moved=0,
        members_recovered=0,
        journal_path=journal,
        check_only=check_only,
    )


def test_apply_check_only_cli_prints_root_bound_token_as_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    args, prepared = _apply_paths(tmp_path)
    monkeypatch.setattr(cli, "prepare_apply", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(cli, "approval_token", lambda *_args: "TOKEN")
    monkeypatch.setattr(
        cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, False),
    )
    monkeypatch.setattr(
        cli,
        "execute_apply",
        lambda *_args, **_kwargs: _apply_result(check_only=True),
    )

    assert main([*args, "--check-only", "--json"]) == 0

    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["check_only"] is True
    assert payload["confirmation_token"] == "TOKEN"


def test_apply_cli_accepts_only_exact_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    args, prepared = _apply_paths(tmp_path)
    journal = tmp_path / "apply.jsonl"
    monkeypatch.setattr(cli, "prepare_apply", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(cli, "approval_token", lambda *_args: "TOKEN")
    monkeypatch.setattr(
        cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, False),
    )
    monkeypatch.setattr(
        cli,
        "execute_apply",
        lambda *_args, **_kwargs: _apply_result(check_only=False, journal=journal),
    )

    assert (
        main(
            [
                *args,
                "--journal",
                str(journal),
                "--confirm-apply",
                "WRONG",
            ]
        )
        == cli.APPLY_FAILED_EXIT
    )
    assert "confirmation does not match" in capsys.readouterr().err

    assert (
        main(
            [
                *args,
                "--journal",
                str(journal),
                "--confirm-apply",
                "TOKEN",
            ]
        )
        == 0
    )
    assert "Apply complete" in capsys.readouterr().out


def test_apply_cli_interactive_confirmation_and_noninteractive_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    args, prepared = _apply_paths(tmp_path)
    journal = tmp_path / "apply.jsonl"
    calls: list[bool] = []
    monkeypatch.setattr(cli, "prepare_apply", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(cli, "approval_token", lambda *_args: "TOKEN")
    monkeypatch.setattr(cli, "total_moving_members", lambda _prepared: 7)
    monkeypatch.setattr(
        cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, False),
    )
    monkeypatch.setattr(
        cli,
        "execute_apply",
        lambda *_args, **kwargs: (
            calls.append(bool(kwargs["resume"]))
            or _apply_result(check_only=False, journal=journal)
        ),
    )

    class Stdin:
        def __init__(self, interactive: bool):
            self.interactive = interactive

        def isatty(self) -> bool:
            return self.interactive

    monkeypatch.setattr(cli.sys, "stdin", Stdin(False))
    assert main([*args, "--journal", str(journal)]) == cli.APPLY_FAILED_EXIT
    assert "non-interactive apply requires" in capsys.readouterr().err

    monkeypatch.setattr(cli.sys, "stdin", Stdin(True))
    monkeypatch.setattr("builtins.input", lambda _prompt: " TOKEN ")
    assert main([*args, "--journal", str(journal), "--resume"]) == 0
    output = capsys.readouterr().out
    assert "atomically move 7 files" in output
    assert "Confirmation token:\nTOKEN" in output
    assert calls == [True]


def test_apply_check_only_cli_prints_text_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    args, prepared = _apply_paths(tmp_path)
    monkeypatch.setattr(cli, "prepare_apply", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(cli, "approval_token", lambda *_args: "TOKEN")
    monkeypatch.setattr(cli, "total_moving_members", lambda _prepared: 7)
    monkeypatch.setattr(
        cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, False),
    )
    monkeypatch.setattr(
        cli,
        "execute_apply",
        lambda *_args, **_kwargs: _apply_result(check_only=True),
    )

    assert main([*args, "--check-only"]) == 0
    output = capsys.readouterr().out
    assert "members=7" in output
    assert "Confirmation token:\nTOKEN" in output


def test_apply_cli_rejects_dirty_runtime_and_reports_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    args, prepared = _apply_paths(tmp_path)
    monkeypatch.setattr(cli, "prepare_apply", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(
        cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "c" * 40, True),
    )

    assert main([*args, "--check-only"]) == cli.APPLY_FAILED_EXIT
    assert "dirty source checkout" in capsys.readouterr().err

    monkeypatch.setattr(
        cli,
        "detect_source_revision",
        lambda: SourceRevision("git", "d" * 40, False),
    )
    assert main([*args, "--check-only"]) == cli.APPLY_FAILED_EXIT
    assert "revision does not match" in capsys.readouterr().err

    monkeypatch.setattr(
        cli,
        "detect_source_revision",
        lambda: SourceRevision("unavailable", None, None),
    )
    assert main([*args, "--check-only"]) == cli.APPLY_FAILED_EXIT
    assert "verifiable clean Git" in capsys.readouterr().err

    monkeypatch.setattr(
        cli,
        "prepare_apply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert main([*args, "--check-only"]) == 130
    assert "Apply interrupted" in capsys.readouterr().err


def test_apply_cli_surfaces_preparation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    args, _prepared = _apply_paths(tmp_path)
    monkeypatch.setattr(
        cli,
        "prepare_apply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ApplyExecutionError("synthetic refusal")
        ),
    )

    assert (
        main([*args, "--journal", args[1], "--confirm-apply", "TOKEN"])
        == cli.APPLY_FAILED_EXIT
    )
    assert "journal must be distinct" in capsys.readouterr().err

    assert main([*args, "--check-only"]) == cli.APPLY_FAILED_EXIT
    assert "synthetic refusal" in capsys.readouterr().err
