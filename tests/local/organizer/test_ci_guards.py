"""Self-tests for the organizer's offline and filesystem safety boundary."""

from __future__ import annotations

import os
import shutil
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

from mnamer.organizer.cli import main

pytestmark = pytest.mark.local


def test_organizer_tests_are_selected_by_dedicated_marker(
    request: pytest.FixtureRequest,
) -> None:
    assert request.node.get_closest_marker("organizer") is not None


def test_real_jellyfin_root_is_blocked_even_for_reads() -> None:
    with pytest.raises(AssertionError, match="never access"):
        Path(r"D:\Jellyfin\Media\Shows").exists()


@pytest.mark.parametrize(
    ("operation",),
    [
        (lambda path: path.write_text("unsafe", encoding="utf-8"),),
        (lambda path: path.unlink(missing_ok=True),),
        (lambda path: os.remove(path=path),),
        (lambda path: shutil.move(path, path.with_suffix(".moved")),),
    ],
)
def test_mutations_outside_tmp_path_are_blocked(
    tmp_path: Path, operation: Callable[[Path], object]
) -> None:
    outside = tmp_path.parent / "outside-organizer-sandbox.mkv"
    with pytest.raises(AssertionError, match="escaped tmp_path"):
        operation(outside)


def test_mutations_inside_tmp_path_are_allowed(tmp_path: Path) -> None:
    video = tmp_path / "synthetic-show.mkv"
    video.write_bytes(b"")
    renamed = video.with_name("synthetic-show-renamed.mkv")
    video.rename(renamed)
    renamed.unlink()


def test_live_requests_are_blocked() -> None:
    with pytest.raises(AssertionError, match="checked-in provider snapshots"):
        requests.get("https://api.tvmaze.com/shows/1", timeout=1)


def test_raw_socket_connections_are_blocked() -> None:
    with pytest.raises(AssertionError, match="checked-in provider snapshots"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_plan_scaffold_uses_no_move_or_delete_calls(
    organizer_test_guard: Any,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["plan"])

    assert exc_info.value.code == 2
    destructive_names = {
        "move",
        "remove",
        "removedirs",
        "rename",
        "replace",
        "rmdir",
        "rmtree",
        "unlink",
    }
    assert not [
        mutation
        for mutation in organizer_test_guard.mutations
        if mutation[0] in destructive_names
    ]


def test_organizer_source_does_not_embed_private_media_root() -> None:
    source_root = Path(__file__).parents[3] / "mnamer" / "organizer"
    for source in source_root.rglob("*.py"):
        assert "d:\\jellyfin" not in source.read_text(encoding="utf-8").casefold()


def test_determinism_helper_hashes_two_identical_runs(
    deterministic_digest: Callable[[Callable[[], bytes]], str],
) -> None:
    calls = 0

    def produce() -> bytes:
        nonlocal calls
        calls += 1
        return os.linesep.join(["synthetic", "plan"]).encode()

    digest = deterministic_digest(produce)

    assert calls == 2
    assert len(digest) == 64
