"""Opt-in CI guards; ordinary local pytest behaviour remains unchanged."""
import socket
import threading
import weakref
from pathlib import Path
import pytest

DATABASE_FILES = frozenset({
    "test_stage07_postgres.py", "test_stage07_pgvector.py", "test_stage09_quality_postgres.py",
})
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_allowed_ports = set()
_original_socketpair = socket.socketpair
_state = threading.local()
_original_bind = socket.socket.bind
_bound = weakref.WeakKeyDictionary()


def guard_address(address):
    if isinstance(address, tuple):
        host, port = address[:2]
        if str(host) in {"127.0.0.1", "::1", "localhost"} and int(port) in (_allowed_ports | set(_bound.values())):
            return
    raise ConnectionRefusedError("CI forbids network access outside its isolated database")


def _connect(sock, address):
    if not getattr(_state, "making_socketpair", False):
        guard_address(address)
    return _original_connect(sock, address)


def _connect_ex(sock, address):
    guard_address(address)
    return _original_connect_ex(sock, address)


def _bind(sock, address):
    result = _original_bind(sock, address)
    if isinstance(address, tuple) and str(address[0]) in {"127.0.0.1", "::1", "localhost"} and address[1] == 0:
        # Existing provider tests use real loopback HTTP servers with fake responses.
        _bound[sock] = sock.getsockname()[1]
    return result


def _socketpair(*args, **kwargs):
    # Windows asyncio creates its private wake-up pair through a loopback connect.
    # Exempt only this synchronous socketpair call, never general loopback traffic.
    previous = getattr(_state, "making_socketpair", False)
    _state.making_socketpair = True
    try:
        return _original_socketpair(*args, **kwargs)
    finally:
        _state.making_socketpair = previous


def pytest_addoption(parser):
    parser.addoption("--ci-db-port", type=int, default=0)


def pytest_configure(config):
    port = config.getoption("--ci-db-port")
    if port:
        _allowed_ports.add(port)
    socket.socket.connect = _connect
    socket.socketpair = _socketpair
    socket.socket.bind = _bind
    socket.socket.connect_ex = _connect_ex


def pytest_unconfigure(config):
    socket.socket.connect = _original_connect
    socket.socketpair = _original_socketpair
    socket.socket.bind = _original_bind
    _bound.clear()
    socket.socket.connect_ex = _original_connect_ex
    _allowed_ports.clear()
