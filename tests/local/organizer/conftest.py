"""Fail-closed safety fixtures for every organizer test.

These guards deliberately live in the organizer test subtree so they are
automatic for current and future organizer regression tests without changing
mnamer's existing unit and end-to-end suites.
"""

from __future__ import annotations

import builtins
import hashlib
import os
import shutil
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import requests


class OrganizerSafetyViolation(AssertionError):
    """Raised when an organizer test crosses a protected boundary."""


@dataclass
class OrganizerTestGuard:
    """Observable state exposed to tests that need to assert no side effects."""

    sandbox: Path
    mutations: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    network_attempts: list[str] = field(default_factory=list)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test below this directory for the dedicated CI selection."""
    organizer_tests = Path(__file__).parent.resolve(strict=False)
    for item in items:
        if Path(str(item.path)).resolve(strict=False).is_relative_to(organizer_tests):
            item.add_marker(pytest.mark.organizer)


def _path_text(value: object) -> str | None:
    if isinstance(value, int):
        return None
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    try:
        return os.fsdecode(os.fspath(value))
    except TypeError:
        return None


def _is_real_media_path(value: object) -> bool:
    text = _path_text(value)
    if text is None:
        return False
    normalized = text.replace("/", "\\").casefold()
    if normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]
    protected_root = "d:\\jellyfin"
    return normalized == protected_root or normalized.startswith(protected_root + "\\")


def _assert_not_real_media(value: object) -> None:
    if _is_real_media_path(value):
        raise OrganizerSafetyViolation(
            "organizer tests must never access the real D:\\Jellyfin media root"
        )


def _assert_in_sandbox(value: object, sandbox: Path) -> None:
    _assert_not_real_media(value)
    text = _path_text(value)
    if text is None:
        return
    candidate = Path(text).resolve(strict=False)
    try:
        candidate.relative_to(sandbox)
    except ValueError as exc:
        raise OrganizerSafetyViolation(
            f"organizer test mutation escaped tmp_path: {candidate}"
        ) from exc


@pytest.fixture(autouse=True)
def organizer_test_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[OrganizerTestGuard]:
    """Block real media access, live network calls, and unsafe mutations."""
    sandbox = tmp_path.resolve(strict=False)
    guard = OrganizerTestGuard(sandbox=sandbox)

    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.setenv("USERPROFILE", str(sandbox))
    monkeypatch.setenv("TMP", str(sandbox))
    monkeypatch.setenv("TEMP", str(sandbox))
    monkeypatch.setenv("MNAMER_ORGANIZER_OFFLINE", "1")
    for variable in (
        "API_KEY_OMDB",
        "API_KEY_TMDB",
        "API_KEY_TVDB",
        "API_KEY_TVMAZE",
    ):
        monkeypatch.delenv(variable, raising=False)

    original_open = builtins.open
    original_path_open = Path.open
    original_os_open = os.open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        _assert_not_real_media(file)
        if any(flag in mode for flag in "wax+"):
            _assert_in_sandbox(file, sandbox)
            guard.mutations.append(("open", (str(file),)))
        return original_open(file, mode, *args, **kwargs)

    def guarded_path_open(
        path: Path, mode: str = "r", *args: Any, **kwargs: Any
    ) -> Any:
        _assert_not_real_media(path)
        if any(flag in mode for flag in "wax+"):
            _assert_in_sandbox(path, sandbox)
            guard.mutations.append(("path-open", (str(path),)))
        return original_path_open(path, mode, *args, **kwargs)

    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        _assert_not_real_media(path)
        if flags & write_flags:
            _assert_in_sandbox(path, sandbox)
            guard.mutations.append(("os-open", (str(path),)))
        return original_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(os, "open", guarded_os_open)

    def guard_single_path(module: Any, name: str) -> None:
        original = getattr(module, name)

        def wrapper(path: Any, *args: Any, **kwargs: Any) -> Any:
            _assert_not_real_media(path)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    for name in ("access", "listdir", "lstat", "scandir", "stat"):
        guard_single_path(os, name)
    for name in (
        "exists",
        "glob",
        "is_dir",
        "is_file",
        "iterdir",
        "lstat",
        "read_bytes",
        "read_text",
        "rglob",
        "stat",
    ):
        guard_single_path(Path, name)

    def guard_mutation(
        module: Any,
        name: str,
        path_parameters: tuple[tuple[int, str], ...],
    ) -> None:
        original = getattr(module, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            checked: list[str] = []
            for position, keyword in path_parameters:
                if position < len(args):
                    value = args[position]
                elif keyword in kwargs:
                    value = kwargs[keyword]
                else:
                    continue
                _assert_in_sandbox(value, sandbox)
                checked.append(str(value))
            guard.mutations.append((name, tuple(checked)))
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    for name in ("mkdir", "makedirs", "removedirs"):
        guard_mutation(os, name, ((0, "name"),))
    for name in ("remove", "rmdir", "unlink"):
        guard_mutation(os, name, ((0, "path"),))
    for name in ("rename", "replace"):
        guard_mutation(os, name, ((0, "src"), (1, "dst")))
    for name in ("mkdir", "rmdir", "touch", "unlink", "write_bytes", "write_text"):
        guard_mutation(Path, name, ((0, "self"),))
    for name in ("rename", "replace"):
        guard_mutation(Path, name, ((0, "self"), (1, "target")))
    for name in ("copy", "copy2", "copyfile", "copytree"):
        guard_mutation(shutil, name, ((1, "dst"),))
    for name in ("move",):
        guard_mutation(shutil, name, ((0, "src"), (1, "dst")))
    for name in ("rmtree",):
        guard_mutation(shutil, name, ((0, "path"),))

    def deny_network(destination: object) -> None:
        guard.network_attempts.append(repr(destination))
        raise OrganizerSafetyViolation(
            f"organizer PR tests must use checked-in provider snapshots: {destination!r}"
        )

    def guarded_create_connection(address: object, *args: Any, **kwargs: Any) -> Any:
        deny_network(address)

    def guarded_socket_connect(
        sock: socket.socket, address: object, *args: Any, **kwargs: Any
    ) -> Any:
        del sock, args, kwargs
        deny_network(address)

    def guarded_request(
        session: requests.Session, method: str, url: str, *args: Any, **kwargs: Any
    ) -> Any:
        del session, args, kwargs
        deny_network(f"{method} {url}")

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_socket_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_socket_connect)
    monkeypatch.setattr(requests.sessions.Session, "request", guarded_request)

    yield guard


@pytest.fixture
def deterministic_digest() -> Callable[[Callable[[], bytes]], str]:
    """Run a byte-producing operation twice and require identical SHA-256 output."""

    def assert_deterministic(producer: Callable[[], bytes]) -> str:
        first = hashlib.sha256(producer()).hexdigest()
        second = hashlib.sha256(producer()).hexdigest()
        assert first == second, "warm-cache output must be byte-for-byte deterministic"
        return first

    return assert_deterministic
