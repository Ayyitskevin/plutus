"""Shared pytest fixtures — isolate tests from production .env Postgres and the network.

The network guard makes CI *enforce* the mock-only contract: any attempt to open a
real connection to a non-loopback host raises, so an accidental live model/API call
fails loudly instead of silently reaching out. Loopback (the Postgres CI service) and
AF_UNIX are allowed; TestClient is in-process ASGI and SQLite is file-based, so neither
opens a real socket.
"""
from __future__ import annotations

import socket

import pytest

from app import config

# --- mock-only network guard -------------------------------------------------

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex

# External connection attempts blocked during the current test (cleared per test).
blocked_attempts: list[str] = []


class BlockedNetworkCall(RuntimeError):
    """Raised when a test tries to open a non-loopback network connection."""


def _host_of(addr: object) -> str | None:
    if isinstance(addr, tuple) and addr:
        return str(addr[0])
    return None


def _is_loopback(host: str | None) -> bool:
    return host is not None and (
        host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")
    )


def _allowed(sock: socket.socket, addr: object) -> bool:
    # Non-INET families (AF_UNIX etc.) are local IPC — always allowed.
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return True
    return _is_loopback(_host_of(addr))


def _guarded_connect(self, addr, *args, **kwargs):  # type: ignore[no-untyped-def]
    if not _allowed(self, addr):
        blocked_attempts.append(_host_of(addr) or repr(addr))
        raise BlockedNetworkCall(
            f"Live network call blocked in tests: {addr!r}. CI is mock-only — "
            "patch httpx / the client instead of reaching the network."
        )
    return _real_connect(self, addr, *args, **kwargs)


def _guarded_connect_ex(self, addr, *args, **kwargs):  # type: ignore[no-untyped-def]
    if not _allowed(self, addr):
        blocked_attempts.append(_host_of(addr) or repr(addr))
        raise BlockedNetworkCall(
            f"Live network call blocked in tests: {addr!r}. CI is mock-only — "
            "patch httpx / the client instead of reaching the network."
        )
    return _real_connect_ex(self, addr, *args, **kwargs)


# Installed at import time so it covers collection and every test in the session.
socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _reset_blocked_attempts():
    """Reset the per-test record of blocked external connection attempts."""
    blocked_attempts.clear()
    yield


@pytest.fixture()
def network_blocked() -> list[str]:
    """The live list of external connection attempts blocked in this test."""
    return blocked_attempts


# --- backend isolation -------------------------------------------------------


@pytest.fixture(autouse=True)
def _sqlite_test_backend(monkeypatch, request):
    """Use per-test SQLite unless test_db_postgres opts into PLUTUS_TEST_DATABASE_URL."""
    if request.node.fspath.basename == "test_db_postgres.py":
        return
    monkeypatch.setattr(config, "DATABASE_URL", None)
    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
