"""Per-entity, per-window rate limiting, ordered after arbitration -- task 6.3.

The limit is the engine's own suppression stage, and its position is the point:
because it runs *after* arbitration, a command it drops is one that already won
its entity, so the outcome `rate-limited` means "this behaviour was going to act
and the limit stopped it" and not "something else outranked it". Folding the
limit into arbitration priority would collapse those two into one answer, and an
agent that cannot tell "a higher-priority behaviour won" from "the limit was
reached" cannot diagnose a house whose lights will not follow a rule
(`design.md` D6).

The window is a **virtual-clock** window -- measured against the timestamps the
engine passes in, never against a wall clock -- which is what makes "a burst of
five within one window is bounded and a sixth a window later is not" a statement
about a number a scenario sets rather than about a scenario that waits.

The admitted timestamps are retained rather than a counter, because the snapshot
carries the rate-limit windows (`simulation`) and a counter cannot say which of
its admissions are still inside the window.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta


class RateLimitError(Exception):
    """Base for the failures this module defines."""


class InvalidRateLimitError(RateLimitError):
    """A bound or window that admits nothing, ever.

    A bound below one or a window at or below zero is not a strict limit, it is a
    prohibition -- and a prohibition expressed as a rate limit would suppress
    every command to an entity forever while the log called it `rate-limited`,
    which reads as a tuning problem rather than as the mistake it is.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


class RateLimiter:
    """At most `bound` admissions per entity per `window` of virtual time."""

    def __init__(
        self,
        *,
        bound: int,
        window: timedelta,
        windows: Mapping[str, Iterable[datetime]] | None = None,
    ) -> None:
        if bound < 1:
            raise InvalidRateLimitError(
                f"a rate limit bound must be at least 1, not {bound}"
            )
        if window <= timedelta(0):
            raise InvalidRateLimitError(
                f"a rate limit window must be positive, not {window!r}"
            )
        self._bound = bound
        self._window = window
        self._admitted: dict[str, tuple[datetime, ...]] = (
            {entity_id: tuple(at) for entity_id, at in windows.items()}
            if windows is not None
            else {}
        )

    @property
    def bound(self) -> int:
        """The most admissions one entity may have within a window."""
        return self._bound

    @property
    def window(self) -> timedelta:
        """The virtual interval over which the bound is counted."""
        return self._window

    def admit(self, entity_id: str, *, now: datetime) -> bool:
        """Whether a command to `entity_id` at `now` is within the limit.

        An admitted command is recorded; a refused one is not, so a burst that
        exceeds the bound does not extend its own window by the attempts it
        refused.
        """
        history = _within(
            self._admitted.get(entity_id, ()), now=now, window=self._window
        )
        if len(history) >= self._bound:
            self._admitted[entity_id] = history
            return False
        self._admitted[entity_id] = (*history, now)
        return True

    def windows(self) -> Mapping[str, tuple[datetime, ...]]:
        """The admissions still inside their window, ordered by entity id.

        Also the form the snapshot carries. The admissions reported are the ones
        retained, including any that have aged out since the last admission --
        `admit` is what prunes, and a snapshot may be taken between admissions,
        so the caller restoring it must not assume the tuples are all fresh.
        """
        return {
            entity_id: self._admitted[entity_id] for entity_id in sorted(self._admitted)
        }

    def forget(self, entity_id: str) -> None:
        """Drop an entity's admissions, as a restore or a removal may require."""
        self._admitted.pop(entity_id, None)


def _within(
    admissions: tuple[datetime, ...], *, now: datetime, window: timedelta
) -> tuple[datetime, ...]:
    """The admissions strictly younger than `window` at `now`.

    An admission exactly one window old is outside it: the bound is "per window",
    so a command at `t` and another at `t + window` are in different windows and
    the second is admitted even at a bound of one.
    """
    return tuple(at for at in admissions if now - at < window)
