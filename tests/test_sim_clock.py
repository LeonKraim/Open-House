"""The virtual clock -- task 3.0.

Time is a number the caller moves, and these tests state that as behaviour: an
observer that reads the clock sees the instant change by exactly the interval it
was advanced, and sees nothing move when nothing advanced it. Each test names the
implementation it would falsify, because a clock test that only asserts `now`
returns a datetime passes against a clock that reads the wall clock, which is the
one implementation the phase forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sim.clock import VirtualClock

#: A fixed instant, so nothing here depends on when the suite runs.
START = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class _Observer:
    """A stand-in for the engine's view of time.

    The engine core lands in the phase's later tasks, so the test supplies the
    smallest thing that reads time the way the engine will: through the one clock
    it was handed. A falsifying engine is not really available to import yet, and
    a clock test that skipped the indirection would be asserting the clock to
    itself.
    """

    clock: VirtualClock

    def observed(self) -> datetime:
        return self.clock.now


def test_advancing_moves_the_instant_the_observer_reads() -> None:
    """The engine observes the instant move by exactly the interval advanced.

    A falsifying implementation is a clock whose `now` reads the wall clock: the
    two observations would then differ by the microseconds the test took rather
    than by exactly ten minutes, and the second assertion would fail. A clock
    that moved on its own would fail the first assertion, before any advance.
    """
    clock = VirtualClock.started_at(START)
    observer = _Observer(clock)
    assert observer.observed() == START

    clock.advance(timedelta(minutes=10))

    assert observer.observed() == START + timedelta(minutes=10)


def test_time_does_not_move_without_an_advance() -> None:
    """Two reads with no advance between them are the same instant.

    Falsified by a clock that reseeds from the wall clock on every read, or that
    advances on read rather than on request; both return two different instants
    here and the phase's "no instant is observed to have passed before
    `advance_time` was called" would not hold.
    """
    clock = VirtualClock.started_at(START)
    observer = _Observer(clock)

    first = observer.observed()
    second = observer.observed()

    assert first == second == START


def test_advancing_by_zero_changes_nothing() -> None:
    """A zero interval is a legal no-op.

    This is the property the scenario runner asserts as "advancing time by zero
    changes nothing". A falsifying clock is one whose advance reseeds or rounds
    its instant, so a zero delta still yields a different reading.
    """
    clock = VirtualClock.started_at(START)

    assert clock.advance(timedelta(0)) == START
    assert clock.now == START


def test_two_clocks_do_not_share_a_timeline() -> None:
    """Advancing one clock leaves an identically-started clock alone.

    Falsified by a clock whose instant is stored on the class rather than the
    instance, or by module-level mutable state; the second clock would then move
    with the first.
    """
    first = VirtualClock.started_at(START)
    second = VirtualClock.started_at(START)

    first.advance(timedelta(hours=3))

    assert first.now == START + timedelta(hours=3)
    assert second.now == START


def test_a_naive_start_instant_is_read_as_utc() -> None:
    """A fixture that writes a bare `2026-01-01T00:00:00` means midnight UTC.

    Falsified by a clock that keeps the naive `datetime` as given: `now.tzinfo`
    would be `None`, and the sun position one layer down, which needs an aware
    instant, would be uncomputable.
    """
    clock = VirtualClock.started_at(datetime(2026, 1, 1))

    assert clock.now.tzinfo is not None
    assert clock.now == START


def test_the_wall_clock_start_is_timezone_aware() -> None:
    """The one system-clock read seeds an aware instant, in UTC.

    Falsified by `datetime.now()` with no zone argument, which yields a naive
    instant, or by a start in some other zone, which would make the instant
    depend on the machine the suite runs on.
    """
    clock = VirtualClock.from_wall_clock()

    assert clock.now.tzinfo is not None
    assert clock.now.utcoffset() == timedelta(0)


def test_the_instant_round_trips_through_isoformat() -> None:
    """The clock's position survives a snapshot as an ISO-8601 string.

    This pins the form `sim/snapshot.py` carries the clock in: the instant is
    serialisable and restores to an equal clock. Falsified by a clock whose
    instant is naive -- `isoformat` would drop the offset, and the restored
    instant would no longer equal the original, which is what "restore resumes
    decision-making identically" would then fail on.
    """
    clock = VirtualClock.started_at(START + timedelta(minutes=3))

    restored = VirtualClock.started_at(datetime.fromisoformat(clock.now.isoformat()))

    assert restored.now == clock.now
