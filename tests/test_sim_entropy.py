"""The seeded random stream -- task 3.1.

The seed is a single control: fix it and a run replays, change it and it does
not, and the stream's position is a count that a snapshot can carry. Each test
names the implementation it would falsify -- most often a stream that ignores its
seed or restarts its sequence on restore, which is exactly what makes a
Hypothesis counterexample irreproducible in practice.
"""

from __future__ import annotations

import pytest

from sim.entropy import RandomStream


def _run(seed: int, draws: int = 20) -> tuple[float, ...]:
    """One run at a seed: draw `draws` values and return them.

    A run is the whole unit here. Asserting two `RandomStream`s are `==` would
    test nothing about determinism; asserting two runs produce the same values is
    the claim the phase makes.
    """
    stream = RandomStream.from_seed(seed)
    return tuple(stream.draw() for _ in range(draws))


def test_the_same_seed_replays_identically() -> None:
    """Two runs at one seed produce identical outputs.

    Falsified by a stream seeded from the wall clock, from `os.urandom`, or from
    anything but the seed it was given; the two runs would differ.
    """
    assert _run(42) == _run(42)


def test_different_seeds_diverge() -> None:
    """Two runs at different seeds are not identical.

    This is what stops the test above from being vacuous. A stream that ignored
    its seed -- returning the same sequence for every seed -- would pass
    `same_seed_replays` and fail here.
    """
    assert _run(1) != _run(2)


def test_a_snapshot_position_resumes_the_sequence() -> None:
    """Restoring a recorded position continues the sequence, not restarts it.

    Falsified by a restore that re-seeds and starts from the beginning: the
    resumed values would be the run's first ten draws, not its next ten, and
    would differ from `continued`. This is the clause that makes the stream's
    position part of the snapshot meaningful.
    """
    original = RandomStream.from_seed(1234)
    for _ in range(7):
        original.draw()
    seed, position = original.seed, original.position
    continued = tuple(original.draw() for _ in range(10))

    resumed = RandomStream.restored(seed, position)

    assert resumed.position == position
    assert tuple(resumed.draw() for _ in range(10)) == continued


def test_position_counts_one_step_per_draw() -> None:
    """The position is the number of draws, and starts at zero.

    Falsified by a stream that counts only some draws (say, `randint` but not
    `draw`), which would make the recorded position too small and the restore
    wrong by however many calls were missed.
    """
    stream = RandomStream.from_seed(0)
    assert stream.position == 0
    stream.draw()
    stream.randint(1, 6)
    stream.choice("abc")
    assert stream.position == 3


def test_restoring_a_negative_position_is_refused() -> None:
    """A position before the start is not a state any stream was ever in."""
    with pytest.raises(ValueError):
        RandomStream.restored(1, -1)


def test_randint_stays_in_range_and_varies() -> None:
    """`randint` never leaves its range and is not a constant.

    Falsified by an implementation that returns the low bound, or the high bound,
    or one value regardless of the draw -- the range check alone would pass a
    constant, so the variation check is the half that has teeth.
    """
    stream = RandomStream.from_seed(9)
    values = [stream.randint(1, 6) for _ in range(200)]
    assert all(1 <= value <= 6 for value in values)
    assert len(set(values)) > 1


def test_randint_refuses_an_empty_range() -> None:
    stream = RandomStream.from_seed(0)
    with pytest.raises(ValueError):
        stream.randint(5, 4)


def test_choice_selects_from_the_options() -> None:
    """`choice` only ever returns one of its options, and more than one of them.

    Falsified by an implementation that ignores the arguments and returns a fixed
    element; the membership check passes a constant, so the variation check is
    again the one that catches it.
    """
    stream = RandomStream.from_seed(3)
    options = ("a", "b", "c")
    picked = {stream.choice(options) for _ in range(100)}
    assert picked <= set(options)
    assert len(picked) > 1


def test_choice_refuses_an_empty_sequence() -> None:
    stream = RandomStream.from_seed(0)
    with pytest.raises(ValueError):
        stream.choice(())


def test_certainty_and_impossibility_are_exact() -> None:
    """`chance(1.0)` always fires and `chance(0.0)` never does.

    Falsified by an inverted comparison, or by a `<` written `<=` against a
    boundary -- both are exhaustive at the ends and invisible in the middle.
    """
    stream = RandomStream.from_seed(5)
    assert all(stream.chance(1.0) for _ in range(20))
    assert not any(stream.chance(0.0) for _ in range(20))
