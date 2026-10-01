"""The virtual clock: the one source of time in the engine and the simulator.

Time in this project is a number the caller moves, not a moment that passes.
`VirtualClock` holds the current virtual instant and moves it only when `advance`
is called, so "the light goes off after a ten-minute quiet period" is a statement
about an interval that was added rather than about a process that slept. Every
module under `engine/` and `sim/` reads time through this object, and none of them
reads the wall clock; `tools/catalog/substrate.check_wall_clock` is what makes
that a check rather than a promise.

The alternative was to run on the real clock and freeze time inside the tests
with a library. It was rejected for the reason `design.md` D3 gives: a freezer in
the test does not stop the engine from holding a wall-clock dependency, so one
code path stays untestable by construction, and the phase's exit criterion wants
an agent to advance time explicitly rather than wait for it.

`from_wall_clock` is the single, deliberate exception. It reads the system clock
exactly once, at construction, to seed a live run's start instant; every read
after that goes through `now`, and a run that must replay fixes its start with
`started_at` instead. The scan permits the wall clock in this module and nowhere
else, so the exception cannot spread by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(slots=True)
class VirtualClock:
    """A clock that moves only when it is told to.

    The instant is timezone-aware and UTC. A naive `datetime` handed to
    `started_at` is read as UTC rather than refused, because a fixture that writes
    `2026-01-01T00:00:00` means midnight UTC and a naive instant would otherwise
    make the sun position uncomputable one layer down.

    `now` is a property and not a method: it names state the clock holds, in the
    same way the random stream's `position` and `seed` do, and it cannot move
    between two reads.
    """

    _instant: datetime

    @classmethod
    def started_at(cls, instant: datetime) -> VirtualClock:
        """A clock fixed at `instant`, the deterministic entry point.

        A run that must replay starts here with a fixture-supplied instant, so
        the seed and the start instant are the only two things a replay needs.
        """
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        return cls(_instant=instant)

    @classmethod
    def from_wall_clock(cls) -> VirtualClock:
        """A clock seeded once from the system clock.

        The only wall-clock read in `engine/` or `sim/`, and it happens here, at
        construction. A live run (the CLI, the MCP server) begins from "now";
        nothing downstream can read the wall clock again, because the commit scan
        forbids it outside this module.
        """
        return cls(_instant=datetime.now(UTC))

    @property
    def now(self) -> datetime:
        """The current virtual instant. It changes only through `advance`.

        This is the clock's snapshot position: `now.isoformat()` is the value
        `sim/snapshot.py` records and `started_at(datetime.fromisoformat(...))` is
        how it comes back, so a restored run continues from the same instant.
        """
        return self._instant

    def advance(self, delta: timedelta) -> datetime:
        """Move the clock forward by `delta` and return the new instant.

        `advance_time` is the control surface's only way to move time, and this is
        the only method that moves it: a caller that wants the engine to observe a
        later instant must call this, because no other path changes `now`. A zero
        `delta` is a legal no-op, which is what makes "advancing time by zero
        changes nothing" a property the runner can assert.
        """
        self._instant = self._instant + delta
        return self._instant
