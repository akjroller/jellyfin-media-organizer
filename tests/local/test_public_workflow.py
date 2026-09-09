"""Run the installed smoke scenarios through the public entry point with coverage."""

from __future__ import annotations

import contextlib
import io
import runpy
from pathlib import Path

import pytest

from jellyfin_show_organizer import (
    cli,
    entrypoint,
    planner,
    quarantine_cli,
    review_execution,
)
from jellyfin_show_organizer.run_provenance import SourceRevision

pytestmark = pytest.mark.local


@pytest.mark.parametrize("duplicate", [False, True])
def test_public_apply_rollback_and_quarantine_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duplicate: bool
):
    namespace = runpy.run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "tools"
            / "installed_workflow_smoke.py"
        )
    )
    smoke = namespace["smoke"]

    def revision() -> SourceRevision:
        return SourceRevision("git", "9" * 40, False)

    # Only revision detection is synthetic; preparation, journals, moves, and
    # command dispatch below use the real implementations and real temp files.
    for module in (cli, entrypoint, planner, quarantine_cli, review_execution):
        monkeypatch.setattr(module, "detect_source_revision", revision)
    monkeypatch.setitem(smoke.__globals__, "detect_source_revision", revision)

    def invoke(args: list[str]) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = entrypoint.main(args)
        assert result == 0, (args[0], result, output.getvalue())
        return output.getvalue()

    monkeypatch.setitem(smoke.__globals__, "cli", invoke)
    smoke(tmp_path, duplicate=duplicate)
