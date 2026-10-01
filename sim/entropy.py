"""The single seeded random stream: the one place randomness enters a run.

A run's randomness is one generator seeded once, so the seed is a single control:
fix it and the run replays, change it and it does not. `specs/simulation/spec.md`
requires exactly one stream for the engine and the simulator together, and the
`tools/catalog/substrate.check_random_stream` scan forbids every other module in
either package from importing `random`, because two generators seeded from one
source diverge the instant one component consumes a number the other did not --
which makes a replay exact only for the components that happened to draw the same
count, and makes a Hypothesis counterexample reproducible in principle and
irreproducible in practice.

The alternative considered was one generator per component, or the `random`
module's functions taking a seed at each call. Both were rejected: the first
breaks the single control, and the second has no position to snapshot.

Every public draw consumes exactly one step of the underlying generator, and the
methods that need more than a coin flip are derived from that one value. That is
what makes the stream's state a plain `(seed, position)` pair: restoring is
seeding and stepping `position` times, which is the form `sim/snapshot.py` can
carry through JSON. The alternative -- exposing the generator's internal state
tuple -- would put a CPython-version-specific structure into the snapshot, and a
snapshot that only restores on the interpreter that wrote it is not a snapshot.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

T = TypeVar("T")


@dataclass(slots=True)
class RandomStream:
    """One seeded generator whose whole observable state is a seed and a count.

    Construct with `from_seed` (a fresh run) or `restored` (a run resumed from a
    snapshot). Read the position to snapshot it; every draw advances it by one.
    """

    _seed: int
    _generator: random.Random
    _position: int

    @classmethod
    def from_seed(cls, seed: int) -> RandomStream:
        """A stream at position zero, seeded for a run."""
        return cls(_seed=seed, _generator=random.Random(seed), _position=0)

    @classmethod
    def restored(cls, seed: int, position: int) -> RandomStream:
        """The stream as it was after `position` draws at `seed`.

        `random.Random` is deterministic given its seed, so stepping the generator
        `position` times reproduces the exact state the original held -- the same
        construction the generator itself uses, and one that keeps the snapshot a
        pair of integers rather than an interpreter-specific tuple.
        """
        if position < 0:
            raise ValueError(f"position must not be negative, got {position}")
        stream = cls.from_seed(seed)
        for _ in range(position):
            stream.draw()
        return stream

    @property
    def seed(self) -> int:
        """The seed the run was started with. Part of the snapshot."""
        return self._seed

    @property
    def position(self) -> int:
        """How many draws have been taken. Part of the snapshot."""
        return self._position

    def draw(self) -> float:
        """The next value in `[0, 1)`, advancing the stream by one step.

        Every other method here is built on this one, so a stream's position is a
        count of draws and nothing else. A caller that reaches past this for the
        underlying generator would make the position a lie, which is why nothing
        outside this module holds the generator.
        """
        value = self._generator.random()
        self._position += 1
        return value

    def chance(self, probability: float) -> bool:
        """Whether an event with `probability` occurred, on this draw."""
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"probability must be in [0, 1], got {probability}")
        return self.draw() < probability

    def randint(self, low: int, high: int) -> int:
        """A uniform integer in `[low, high]` inclusive, on this draw."""
        if high < low:
            raise ValueError(f"empty range for randint: {low}..{high}")
        span = high - low + 1
        return low + min(int(self.draw() * span), span - 1)

    def choice(self, options: Sequence[T]) -> T:
        """One uniformly chosen element of `options`, on this draw."""
        if not options:
            raise ValueError("cannot choose from an empty sequence")
        index = min(int(self.draw() * len(options)), len(options) - 1)
        return options[index]
