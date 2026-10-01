"""A runtime guard that fails a run the moment it opens a socket.

The commit scan in `tools/catalog/substrate.py` reads the import graph, and it
cannot catch a socket reached dynamically -- through `importlib`, a
dependency-supplied string, or a library that decided to phone home -- because
nothing in the source names it. `specs/simulation/spec.md` requires the runtime
half as well: a scenario run executes under a guard that fails if a socket is
opened "however it reached it". This is that guard.

It lives here and not in `sim/` on purpose. `sim/` may not import `socket` -- the
very rule the guard enforces -- so a guard that patches `socket` cannot be a
`sim/` module without breaking the rule it exists to hold. `tools/` is
first-party, unscanned, and already the home of the project's enforcement
tooling, so the one module that must import `socket` sits where that import is
legal. A scenario runner wraps its run in `no_sockets()`, or a caller passes it
through `without_sockets`.

The alternative was to state the promise and rely on review, which
`design.md` D14 rejects for the reason it gives everywhere: an assertion nothing
runs is not an assertion.
"""

from __future__ import annotations

import socket
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence

#: This module's filename, so a stack walk can skip its own frames and name the
#: code that actually tried to open the socket.
_THIS_MODULE = Path(__file__).name


class NetworkAccessError(RuntimeError):
    """Raised when guarded code opens a socket, naming where it did.

    The call site travels on the exception rather than only in the message,
    because the tests and the failing run both read it back, and a caller forced
    to parse a sentence to recover the location would be parsing prose to get
    structured data.
    """

    def __init__(self, call_site: str) -> None:
        super().__init__(f"a socket was opened at {call_site}")
        self.call_site = call_site


def _call_site(stack: Sequence[traceback.FrameSummary]) -> str:
    """The innermost frame outside this module: the code that opened the socket.

    Walked innermost-first, skipping this module's own frames -- `_refuse` and the
    guarded `__init__` are between the caller and the raise -- so the name in the
    finding is the caller's and not a line of the guard.
    """
    for frame in reversed(stack):
        if Path(frame.filename).name != _THIS_MODULE:
            return f"{frame.filename}:{frame.lineno} in {frame.name}"
    return "<unknown>"


def _refuse() -> NoReturn:
    raise NetworkAccessError(_call_site(traceback.extract_stack()))


class _GuardedSocket:
    """A stand-in for `socket.socket` whose construction always fails.

    A plain class rather than a subclass, because the point is to fail before a
    real file descriptor exists -- `socket.socket.__init__` is where the
    descriptor is created, so an override that raised there and a stand-in whose
    construction raises are the same guarantee, and the stand-in cannot
    accidentally inherit a path that opens something.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        _refuse()


def _guarded_create_connection(*args: object, **kwargs: object) -> NoReturn:
    _refuse()


@contextmanager
def no_sockets() -> Generator[None]:
    """Run a block with socket creation disabled.

    Restores the module on the way out, including when the block raises for a
    reason other than the guard, so a guarded run does not leave the interpreter
    unable to open a socket afterwards.
    """
    saved_socket = socket.socket
    saved_create_connection = socket.create_connection
    socket.socket = _GuardedSocket  # pyright: ignore[reportAttributeAccessIssue]
    socket.create_connection = _guarded_create_connection  # pyright: ignore[reportAttributeAccessIssue]
    try:
        yield
    finally:
        socket.socket = saved_socket
        socket.create_connection = saved_create_connection


def without_sockets[T](action: Callable[[], T]) -> T:
    """Run `action` under the guard and return its result.

    The form a runner wants when it has a callable rather than a block: a
    scenario run is one call, and wrapping it here keeps the guard's extent the
    same as the run's.
    """
    with no_sockets():
        return action()
