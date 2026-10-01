"""The runtime socket guard -- task 3.3.

The import scan catches a networking module named in source; the guard catches
one reached at run time, which is the half a scan cannot see. These tests
exercise both directions the spec names: a guard that fails a run which opens a
socket "however it reached it", and a guard that lets a run which opens none
proceed -- because a guard that failed every run would pass the first test and
be useless, and one that restored the module only on the happy path would leak a
patched interpreter into the next test.
"""

from __future__ import annotations

import socket

import pytest

from tools.netguard import NetworkAccessError, no_sockets, without_sockets


def _open_a_socket() -> None:
    socket.socket()


def _connect_out() -> None:
    socket.create_connection(("example.invalid", 443))


def test_opening_a_socket_under_the_guard_fails_naming_the_site() -> None:
    """A direct `socket.socket()` is refused, and the finding names the caller.

    Falsified by a guard that patches only `create_connection` and leaves the
    constructor alone: the most ordinary way to open a socket would slip past,
    and the run would not fail.
    """
    with pytest.raises(NetworkAccessError) as caught, no_sockets():
        _open_a_socket()

    assert "_open_a_socket" in caught.value.call_site


def test_connecting_under_the_guard_fails_naming_the_site() -> None:
    """`create_connection` is refused before it resolves the host.

    Falsified by a guard that patches only the constructor: `create_connection`
    would then fail with the `NetworkAccessError` raised from inside the socket
    module, naming a line of the standard library rather than the caller, and --
    worse -- only after it had already attempted name resolution.
    """
    with pytest.raises(NetworkAccessError) as caught, no_sockets():
        _connect_out()

    assert "_connect_out" in caught.value.call_site


def test_a_run_that_opens_no_socket_passes_under_the_guard() -> None:
    """The guard is not a blanket refusal; work that opens nothing proceeds.

    Falsified by a guard whose guard block raised unconditionally, which is the
    failure mode that makes an enforcement tooling change unusable -- it would
    also refuse every scenario that never touches the network, which is all of
    them.
    """
    assert without_sockets(lambda: 41 + 1) == 42


def test_the_guard_restores_the_socket_module() -> None:
    """Both patched members are put back when the block exits."""
    original_socket = socket.socket
    original_create = socket.create_connection

    with no_sockets():
        assert socket.socket is not original_socket
        assert socket.create_connection is not original_create

    assert socket.socket is original_socket
    assert socket.create_connection is original_create


def test_the_guard_restores_after_an_unrelated_failure() -> None:
    """A block that raises for a reason other than the guard still restores.

    Falsified by restoration written after the `yield` rather than in a `finally`:
    the patch would survive the exception and every later test in the process
    would run with sockets disabled.
    """
    original_socket = socket.socket

    with pytest.raises(ZeroDivisionError), no_sockets():
        raise ZeroDivisionError("boom")

    assert socket.socket is original_socket
