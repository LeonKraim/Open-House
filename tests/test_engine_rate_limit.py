"""Per-entity, per-window rate limiting -- task 6.3.

Two things these tests pin. The window is measured against the virtual clock the
caller passes in, never a wall clock -- so "the window elapsed" is a timestamp
arithmetic fact and not something a test waits for. And a refusal is not an
admission: a burst that exceeds the bound does not extend its own window by the
attempts it refused, which is the mistake that makes a limit decay into a
prohibition under load.

What this module does *not* test is the position of the limit in the pipeline.
"Ordered after arbitration" is a property of the engine's tick, and the test that
a `rate-limited` record is distinguishable from a `lost arbitration` one belongs
with the engine that writes both; here the limit is exercised on its own.

Each test names the behaviour and says, in its docstring, what a falsifying
implementation would look like, because a test that would pass against any
implementation exercises nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.rate_limit import InvalidRateLimitError, RateLimiter

_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)


def _limiter(bound: int = 2, window: timedelta = _MINUTE) -> RateLimiter:
    return RateLimiter(bound=bound, window=window)


def test_a_burst_within_the_bound_is_admitted() -> None:
    """The first `bound` commands to one entity in a window are admitted.

    A falsifying implementation that refused the first command would make the
    limit suppress the very command it exists to allow, and a behaviour would
    never act on an entity the limit names.
    """
    limiter = _limiter(bound=2)
    assert limiter.admit("light.kitchen", now=_AT) is True
    assert limiter.admit("light.kitchen", now=_AT + timedelta(seconds=1)) is True


def test_a_command_beyond_the_bound_is_refused() -> None:
    """The command after the bound is refused, and the refusals are the excess.

    A falsifying implementation that counted commands *per tick* rather than per
    window would admit a fresh burst every tick, which bounds nothing at all --
    a behaviour proposing once per tick would never be limited.
    """
    limiter = _limiter(bound=2)
    limiter.admit("light.kitchen", now=_AT)
    limiter.admit("light.kitchen", now=_AT + timedelta(seconds=1))
    assert limiter.admit("light.kitchen", now=_AT + timedelta(seconds=2)) is False
    assert limiter.admit("light.kitchen", now=_AT + timedelta(seconds=3)) is False


def test_the_bound_is_per_entity() -> None:
    """One entity's burst does not spend another's allowance.

    A falsifying implementation with a single counter would let the kitchen's
    motion lighting exhaust the bedroom's budget, and a house with one busy room
    would lose automation everywhere else.
    """
    limiter = _limiter(bound=1)
    assert limiter.admit("light.kitchen", now=_AT) is True
    assert limiter.admit("light.bedroom", now=_AT) is True
    assert limiter.admit("light.kitchen", now=_AT) is False
    assert limiter.admit("light.bedroom", now=_AT) is False


def test_a_command_one_window_later_is_admitted() -> None:
    """The window is an interval, so exactly one window later starts a new one.

    A falsifying implementation comparing with `<=` would keep the admission
    inside its own window forever at the boundary, and a behaviour proposing at a
    fixed interval equal to the window would be limited from its second command
    onwards.
    """
    limiter = _limiter(bound=1)
    assert limiter.admit("light.kitchen", now=_AT) is True
    assert limiter.admit("light.kitchen", now=_AT + _MINUTE) is True


def test_a_command_inside_the_window_is_refused_even_after_a_gap() -> None:
    """Sliding the window is not the same as resetting it.

    A falsifying implementation that cleared the entity's history on a refusal
    -- or that reset on any admission -- would admit every other command in a
    steady stream, so a behaviour proposing twice a window would pass at a bound
    of one.
    """
    limiter = _limiter(bound=2, window=timedelta(minutes=10))
    limiter.admit("light.kitchen", now=_AT)
    limiter.admit("light.kitchen", now=_AT + timedelta(minutes=9))
    assert (
        limiter.admit("light.kitchen", now=_AT + timedelta(minutes=9, seconds=30))
        is False
    )
    assert limiter.admit("light.kitchen", now=_AT + timedelta(minutes=10)) is True


def test_a_refused_command_does_not_extend_the_window() -> None:
    """A refusal is not an admission, so it cannot postpone the next allowance.

    A falsifying implementation that recorded the refused attempt would make a
    behaviour proposing faster than the window refill it indefinitely -- a
    behaviour proposing every second at a bound of one per minute would never be
    admitted again, which turns a rate limit into a permanent prohibition.
    """
    limiter = _limiter(bound=1, window=_MINUTE)
    limiter.admit("light.kitchen", now=_AT)
    for second in range(1, 60):
        assert (
            limiter.admit("light.kitchen", now=_AT + timedelta(seconds=second)) is False
        )
    assert limiter.admit("light.kitchen", now=_AT + _MINUTE) is True


def test_the_windows_report_the_admissions_within_them() -> None:
    """`windows` is the snapshot form: entity id to the admissions retained.

    A falsifying implementation that exposed a bare counter would leave the
    snapshot unable to say which admissions were still inside their window, and a
    restored limiter would either forget the burst or resume it forever.
    """
    limiter = _limiter(bound=3)
    limiter.admit("light.kitchen", now=_AT)
    limiter.admit("light.bedroom", now=_AT + timedelta(seconds=5))
    assert limiter.windows() == {
        "light.bedroom": (_AT + timedelta(seconds=5),),
        "light.kitchen": (_AT,),
    }


def test_forgetting_an_entity_restores_its_allowance() -> None:
    """An entity can be dropped, as a removal or a restore may require.

    A falsifying implementation with no way to forget would leave a removed
    entity's history in the snapshot, and a re-added entity would inherit a
    limit it never spent.
    """
    limiter = _limiter(bound=1)
    limiter.admit("light.kitchen", now=_AT)
    limiter.forget("light.kitchen")
    assert limiter.admit("light.kitchen", now=_AT) is True


def test_the_bound_and_the_window_are_readable_settings() -> None:
    """The two tunables are properties, because the engine resolves them by key.

    A falsifying implementation that buried them in the constructor would leave
    the record unable to name the bound a command was refused under, and a
    scenario tuning the limit would have no way to read back what it set.
    """
    limiter = _limiter(bound=7, window=timedelta(seconds=90))
    assert limiter.bound == 7
    assert limiter.window == timedelta(seconds=90)


@pytest.mark.parametrize(
    ("bound", "window"),
    [(0, _MINUTE), (-1, _MINUTE), (1, timedelta(0)), (1, -_MINUTE)],
)
def test_a_limit_that_admits_nothing_is_refused(bound: int, window: timedelta) -> None:
    """A bound below one or a window at or below zero is a mistake, not a limit.

    A falsifying implementation that accepted them would suppress every command
    to every entity forever while the log called each one `rate-limited`, which
    an operator reads as a tuning problem rather than as the misconfiguration it
    is.
    """
    with pytest.raises(InvalidRateLimitError):
        RateLimiter(bound=bound, window=window)
