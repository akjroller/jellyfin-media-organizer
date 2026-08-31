from __future__ import annotations

import os
import socket
from typing import NoReturn

import pytest


def _blocked_network(*args: object, **kwargs: object) -> NoReturn:
    del args, kwargs
    raise AssertionError(
        "network access is disabled in the deterministic offline test gate"
    )


@pytest.fixture(autouse=True)
def deny_network_in_offline_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("JMO_TEST_NO_NETWORK") != "1":
        return

    monkeypatch.setattr(socket, "create_connection", _blocked_network)
    monkeypatch.setattr(socket.socket, "connect", _blocked_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_network)
